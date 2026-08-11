"""Daily short-term trade recommendations from trends + momentum."""

import asyncio
from datetime import date, datetime, timezone

import pandas as pd

from agents.technical.indicators import build_trade_levels, enrich_indicators
from database.repositories.daily_trade_repository import DailyTradeRepository
from domain.daily_trade import DailyTradeReport, TradePick
from domain.discovery import DiscoveryCandidate
from providers.interfaces import MarketDataProvider
from services.capital_fit import affordability_bonus, capital_price_policy, discovery_themes_for_capital
from services.committee_consensus import evaluate_consensus, is_actionable_source
from services.company_discovery_service import CompanyDiscoveryService
from services.macro_regime_service import MacroRegimeService
from services.market_dashboard_service import MarketDashboardService
from services.micro_portfolio_manager_service import MicroPortfolioManagerService
from services.risk_policy_service import RiskPolicyService
from utils.logging import get_logger

logger = get_logger(__name__)

_COMMITTEE_DAILY_SCREEN = 16
_COMMITTEE_DAILY_CONCURRENCY = 3

_SHORT_TERM_THEMES = (
    "momentum breakout stock today",
    "earnings beat surge short term",
    "reddit trending stock today",
    "small cap volume spike breakout",
    "biotech catalyst FDA approval",
    "AI semiconductor momentum",
)

_ACTION_BUY = "compra"
_ACTION_SWING = "swing corto"
_ACTION_WATCH = "vigilar"


class DailyTradeRecommendationService:
    """Generates daily short-term picks combining social trends and technical momentum."""

    def __init__(
        self,
        market_provider: MarketDataProvider,
        discovery_service: CompanyDiscoveryService,
        trade_repo: DailyTradeRepository | None = None,
        analysis_service: object | None = None,
    ) -> None:
        self._market = market_provider
        self._discovery = discovery_service
        self._repo = trade_repo
        self._analysis = analysis_service
        self._dashboard = MarketDashboardService()
        self._macro = MacroRegimeService()
        self._risk = RiskPolicyService(self._macro)

    async def generate(
        self,
        session: str = "pre_market",
        max_picks: int = 8,
        exclude_tickers: list[str] | None = None,
        persist: bool = True,
        capital: float | None = None,
    ) -> DailyTradeReport:
        logger.info("daily_trade.generate.start", session=session, capital=capital)

        regime = await self._fetch_market_regime()
        macro = await self._macro.assess(market_regime=regime)
        policy = self._risk.policy_from_settings()
        risk_notes: list[str] = [macro.thesis]
        if macro.block_reason:
            risk_notes.append(macro.block_reason)

        # Micro books: few whole-share positions, not 8 tiny lines
        if capital and capital <= 100:
            max_picks = min(max_picks, 1 if capital <= 25 else (3 if capital <= 60 else 4))
        if macro.mode in ("risk_off", "crisis"):
            max_picks = min(max_picks, 2 if capital and capital <= 100 else 4)
            risk_notes.append(
                f"Régimen {macro.mode}: menos picks y tamaño ×{macro.size_multiplier:.2f}."
            )

        price_policy = capital_price_policy(capital or 1000, target_positions=max_picks)
        micro_mode = bool(capital and capital <= 500)
        committee_mode = "micro" if (capital and capital <= 100) else "strict"
        used_manager = False
        committee_notes: list[str] = []
        picks: list[TradePick] = []

        # Ultra-micro: skip slow social discovery — capital desk hunt only (latency)
        if capital and capital <= 30 and macro.mode != "crisis":
            manager = MicroPortfolioManagerService(
                self._market,
                self._discovery,
                analysis_service=self._analysis,
            )
            plan = await manager.manage(
                capital=capital,
                exclude_tickers=exclude_tickers,
                max_candidates=18,
            )
            if plan.warnings:
                committee_notes.extend(plan.warnings)
            if plan.picks:
                used_manager = True
                picks = list(plan.picks)[:max_picks]
                summary = plan.summary
            else:
                summary = (
                    f"{price_policy.description_es} Caza continua sin consenso aún. "
                    f"{' '.join(plan.warnings[:2])}"
                )
            picks = self._risk.filter_picks_for_regime(
                picks,
                size_multiplier=macro.size_multiplier,
                mode=macro.mode,
            )
            picks = [
                p for p in picks
                if p.committee_unanimous
                or is_actionable_source(p.sources)
                or p.action == _ACTION_WATCH
            ]
            if committee_notes:
                risk_notes.extend(committee_notes[:4])
            if macro.thesis:
                summary = f"{summary} | Macro: {macro.thesis}"
            report = DailyTradeReport(
                report_date=date.today(),
                generated_at=datetime.now(timezone.utc),
                session=session,
                market_regime=regime,
                macro_mode=macro.mode,
                macro_bias=macro.macro_bias,
                macro_thesis=macro.thesis,
                size_multiplier=macro.size_multiplier,
                risk_notes=risk_notes,
                summary=summary,
                picks=picks,
            )
            if persist and self._repo:
                await self._repo.save(report)
            logger.info(
                "daily_trade.generate.done",
                picks=len(picks),
                session=session,
                micro_manager=True,
                fast_path=True,
                macro_mode=macro.mode,
            )
            return report

        themes = discovery_themes_for_capital(price_policy, list(_SHORT_TERM_THEMES))
        # Defensive themes overlay when risk-off
        if macro.mode in ("risk_off", "crisis"):
            themes = [
                "defensive dividend stock low volatility",
                "consumer staples ETF constituents",
                *themes[:3],
            ]

        discovery = await self._discovery.research(
            themes=themes,
            max_candidates=25,
            exclude_tickers=exclude_tickers or [],
            max_price=price_policy.max_share_price if micro_mode else None,
        )

        scored: list[TradePick] = []
        min_score = 12 if (capital and capital <= 100) else (22 if micro_mode else 35)
        if macro.mode == "risk_off":
            min_score += 8
        elif macro.mode == "crisis":
            min_score += 20

        for candidate in discovery.candidates[:20]:
            pick = await self._score_candidate(
                candidate,
                min_score=min_score,
                allow_watch=micro_mode or macro.mode in ("risk_off", "crisis"),
            )
            if pick:
                if capital:
                    line = (capital * 0.80) / max(max_picks, 1)
                    pick.score = pick.score + affordability_bonus(
                        pick.current_price or 0, line, price_policy
                    )
                    if (
                        price_policy.max_share_price
                        and pick.current_price
                        and pick.current_price > price_policy.max_share_price
                    ):
                        continue
                # Reward/risk filter
                if (
                    pick.entry_price
                    and pick.stop_loss
                    and pick.target_price
                    and pick.entry_price > pick.stop_loss
                ):
                    rr = (pick.target_price - pick.entry_price) / (
                        pick.entry_price - pick.stop_loss
                    )
                    if rr < policy.min_reward_risk and pick.action != _ACTION_WATCH:
                        pick.risks = list(pick.risks) + [
                            f"Reward/risk {rr:.2f} < mínimo {policy.min_reward_risk:.1f}."
                        ]
                        if macro.mode != "risk_on":
                            continue
                scored.append(pick)

        scored.sort(key=lambda p: p.score, reverse=True)
        # PASO 01: higher-timeframe uptrend gate (weekly + monthly)
        scored, htf_notes = await self._apply_htf_gate(scored, soft=macro.mode != "risk_on")
        # Committee gate (micro = soft majority; larger books = unanimous)
        picks = await self._apply_committee_gate(
            scored[:_COMMITTEE_DAILY_SCREEN], max_picks, mode=committee_mode
        )
        used_manager = False
        committee_notes = list(htf_notes or [])

        # Capital desk fallback for micro/small books — already committee-gated inside manager
        if capital and capital <= 500 and len(picks) < max(1, max_picks // 2) and macro.mode != "crisis":
            manager = MicroPortfolioManagerService(
                self._market,
                self._discovery,
                analysis_service=self._analysis,
            )
            # Do NOT shrink capital by size_multiplier here — apply sizing later
            plan = await manager.manage(
                capital=capital,
                exclude_tickers=exclude_tickers,
                max_candidates=40,
            )
            if plan.warnings:
                committee_notes.extend(plan.warnings)
            if plan.picks:
                used_manager = True
                seen = {p.ticker for p in plan.picks}
                merged = list(plan.picks)
                for p in picks:
                    if p.ticker not in seen and p.committee_unanimous:
                        merged.append(p)
                        seen.add(p.ticker)
                picks = merged[:max_picks]
                summary = plan.summary
            else:
                summary = self._build_summary(picks, regime, session, macro.mode)
                if plan.warnings:
                    summary += " " + " ".join(plan.warnings)
        else:
            summary = self._build_summary(picks, regime, session, macro.mode)

        picks = self._risk.filter_picks_for_regime(
            picks,
            size_multiplier=macro.size_multiplier,
            mode=macro.mode,
        )
        # Defense: drop ungated buys before publish / autopilot
        picks = [
            p for p in picks
            if p.committee_unanimous
            or is_actionable_source(p.sources)
            or p.action == _ACTION_WATCH
        ]

        if macro.mode == "crisis":
            summary = (
                f"{macro.thesis} Sin nuevas compras recomendadas hasta que baje el estrés macro/VIX. "
                f"Cash objetivo ≈ {macro.cash_target_pct:.0f}%."
            )
            picks = []
        elif capital and not used_manager:
            summary = f"{price_policy.description_es} {summary}"
        if used_manager and not picks:
            summary = (
                f"{price_policy.description_es} Sin consenso del comité "
                "(micro: mayoría BUY corto+largo) en candidatos líquidos hoy. Se mantiene efectivo."
            )
        if not picks and macro.mode != "crisis":
            summary = (
                f"{summary} Regla de firma: solo se recomienda/compra con consenso del comité "
                "en corto y largo plazo (micro = mayoría)."
            )
        if committee_notes and macro.mode != "crisis":
            risk_notes.extend(committee_notes[:3])
        if macro.thesis and macro.mode != "crisis":
            summary = f"{summary} | Macro: {macro.thesis}"

        report = DailyTradeReport(
            report_date=date.today(),
            generated_at=datetime.now(timezone.utc),
            session=session,
            market_regime=regime,
            macro_mode=macro.mode,
            macro_bias=macro.macro_bias,
            macro_thesis=macro.thesis,
            size_multiplier=macro.size_multiplier,
            risk_notes=risk_notes,
            summary=summary,
            picks=picks,
        )

        if persist and self._repo:
            await self._repo.save(report)

        logger.info(
            "daily_trade.generate.done",
            picks=len(picks),
            session=session,
            micro_manager=used_manager,
            macro_mode=macro.mode,
            size_mult=macro.size_multiplier,
        )
        return report

    async def get_latest(self) -> DailyTradeReport | None:
        if not self._repo:
            return None
        return await self._repo.get_latest()

    async def _apply_htf_gate(
        self,
        scored: list[TradePick],
        *,
        soft: bool = False,
    ) -> tuple[list[TradePick], list[str]]:
        """Keep / annotate picks with weekly+monthly uptrend (PASO 01)."""
        from config.settings import get_settings
        from services.htf_trend_filter import HtfTrendFilter

        settings = get_settings()
        notes: list[str] = []
        if not settings.htf_trend_gate_enabled or not scored:
            return scored, notes

        filt = HtfTrendFilter(
            self._market,
            min_confidence=float(settings.htf_trend_min_confidence or 0.5),
        )
        screen_n = max(1, int(settings.htf_trend_max_screen or 12))
        head = scored[:screen_n]
        tail = scored[screen_n:]

        kept: list[TradePick] = []
        rejected = 0
        for pick in head:
            try:
                result = await filt.evaluate(pick.ticker)
            except Exception as exc:
                logger.warning("daily_trade.htf_failed", ticker=pick.ticker, error=str(exc))
                kept.append(pick)
                continue
            tag = f"htf:{result.weekly}/{result.monthly}"
            sources = list(pick.sources or [])
            if tag not in sources:
                sources.append(tag)
            pick.sources = sources
            if result.passed or result.inconclusive:
                kept.append(pick)
            elif soft:
                pick.risks = list(pick.risks or []) + [result.reason]
                if pick.action != _ACTION_WATCH:
                    pick.action = _ACTION_WATCH
                kept.append(pick)
                rejected += 1
            else:
                rejected += 1
        # Soft/hard: do not promote unevaluated tail ahead of HTF survivors
        out = kept + (tail if soft else [])
        if rejected:
            notes.append(
                f"Filtro HTF (1W+1M): {rejected} candidatos sin uptrend fuerte"
                + (" → vigilados" if soft else " descartados")
                + "."
            )
        logger.info(
            "daily_trade.htf_gate",
            screened=len(head),
            kept=len(kept),
            rejected=rejected,
            soft=soft,
        )
        return out, notes

    async def _apply_committee_gate(
        self,
        candidates: list[TradePick],
        max_picks: int,
        *,
        mode: str = "strict",
    ) -> list[TradePick]:
        """Keep picks that pass committee gate (strict unanimous or micro majority)."""
        if not candidates:
            return []
        if not self._analysis:
            logger.warning("daily_trade.committee_unavailable")
            return []

        sem = asyncio.Semaphore(_COMMITTEE_DAILY_CONCURRENCY)
        buy_like = [p for p in candidates if p.action != _ACTION_WATCH]

        async def _gate(pick: TradePick) -> TradePick | None:
            async with sem:
                try:
                    if mode == "micro" and hasattr(self._analysis, "score_for_micro_consensus"):
                        thesis = await self._analysis.score_for_micro_consensus(pick.ticker)
                    else:
                        thesis = await self._analysis.score_for_consensus(pick.ticker)
                except Exception as exc:
                    logger.warning(
                        "daily_trade.committee_failed",
                        ticker=pick.ticker,
                        error=str(exc),
                    )
                    return None
            verdict = evaluate_consensus(thesis, mode=mode)
            if not verdict.passed:
                logger.info(
                    "daily_trade.committee_reject",
                    ticker=pick.ticker,
                    mode=mode,
                    reasons=verdict.reasons[:3],
                )
                return None
            tag = verdict.source_tag
            sources = list(pick.sources or [])
            if tag not in sources:
                sources.append(tag)
            label = "mayoría micro" if mode == "micro" else "unánime"
            return pick.model_copy(
                update={
                    "committee_unanimous": True,
                    "committee_recommendation": verdict.recommendation,
                    "short_horizon_buy": True,
                    "long_horizon_buy": True,
                    "confidence": max(pick.confidence, float(thesis.confidence or 0.55)),
                    "sources": sources,
                    "rationale": (
                        f"Comité {label} BUY corto+largo. {pick.rationale}"
                    ).strip(),
                    "horizon": "corto+largo (comité)",
                }
            )

        # Hunt in batches — don't stop after the first rejects
        approved: list[TradePick] = []
        buy_like = [p for p in candidates if p.action != _ACTION_WATCH]
        batch = 8
        for start in range(0, min(len(buy_like), _COMMITTEE_DAILY_SCREEN), batch):
            if len(approved) >= max_picks:
                break
            chunk = buy_like[start : start + batch]
            gated = await asyncio.gather(*[_gate(p) for p in chunk])
            approved.extend([p for p in gated if p])
            logger.info(
                "daily_trade.committee_hunt",
                screened=start + len(chunk),
                approved=len(approved),
                mode=mode,
            )
        approved.sort(key=lambda p: p.score, reverse=True)
        return approved[:max_picks]

    async def _fetch_market_regime(self) -> str:
        try:
            indices, sectors, _, _ = await asyncio.gather(
                self._dashboard._fetch_indices(),
                self._dashboard._fetch_sector_heatmap(),
                self._dashboard._economic_calendar(),
                self._dashboard._market_news(),
            )
            regime, _ = self._dashboard._compute_market_regime(indices, sectors)
            return regime
        except Exception as exc:
            logger.warning("daily_trade.regime_failed", error=str(exc))
            return "neutral"

    async def _score_candidate(
        self,
        candidate: DiscoveryCandidate,
        min_score: float = 35,
        allow_watch: bool = False,
    ) -> TradePick | None:
        ticker = candidate.ticker
        try:
            quote = await self._market.get_quote(ticker)
            hist = await self._market.get_history(ticker, period="3mo", interval="1d")
            if hist.empty or len(hist) < 25:
                return None

            df = enrich_indicators(hist)
            last = df.iloc[-1]
            price = float(quote.get("current_price") or last["Close"])

            change_1d = self._pct_change(df, 1)
            change_5d = self._pct_change(df, 5)
            rsi = float(last["RSI"]) if pd.notna(last.get("RSI")) else None
            sma20 = float(last["SMA20"]) if pd.notna(last.get("SMA20")) else price
            avg_vol = float(df["Volume"].tail(20).mean())
            vol_spike = float(last["Volume"] / avg_vol) if avg_vol > 0 else 1.0

            macd_hist = float(last["MACD_Hist"]) if pd.notna(last.get("MACD_Hist")) else 0.0
            atr = float(last["ATR"]) if pd.notna(last.get("ATR")) else price * 0.02
            support = float(df["Low"].tail(20).quantile(0.1))
            resistance = float(df["High"].tail(20).quantile(0.9))
            levels = build_trade_levels(price, support, resistance, atr)

            momentum_score = self._momentum_score(change_1d, change_5d, vol_spike, macd_hist)
            technical_score = self._technical_score(price, sma20, rsi, macd_hist)
            social_score = min(candidate.score / 15.0, 1.0) * 100

            total = round(
                social_score * 0.35 + momentum_score * 0.35 + technical_score * 0.30,
                2,
            )

            if total < min_score:
                return None

            action, horizon = self._classify_action(change_1d, change_5d, rsi, vol_spike)
            if action == _ACTION_WATCH and total < 50 and not allow_watch:
                return None

            target = levels.get("take_profit_1") or price * 1.05
            stop = levels.get("stop_loss") or price * 0.97
            expected_return = ((target - price) / price * 100) if price else None

            catalysts = candidate.news_headlines[:3]
            if not catalysts:
                catalysts = [m.text[:80] for m in candidate.mentions[:2]]

            risks = self._build_risks(rsi, vol_spike, change_5d)
            confidence = min(0.95, max(0.35, total / 100))

            return TradePick(
                ticker=ticker,
                company_name=candidate.company_name or quote.get("company_name"),
                action=action,
                horizon=horizon,
                score=total,
                confidence=round(confidence, 2),
                current_price=round(price, 2),
                entry_price=round(price, 2),
                target_price=round(target, 2),
                stop_loss=round(stop, 2),
                expected_return_pct=round(expected_return, 2) if expected_return else None,
                change_1d_pct=round(change_1d, 2) if change_1d is not None else None,
                change_5d_pct=round(change_5d, 2) if change_5d is not None else None,
                volume_spike=round(vol_spike, 2),
                rsi=round(rsi, 1) if rsi is not None else None,
                social_buzz_score=round(candidate.score, 2),
                catalysts=catalysts,
                rationale=self._build_rationale(candidate, change_1d, change_5d, vol_spike, rsi),
                risks=risks,
                sources=candidate.sources,
            )
        except Exception as exc:
            logger.warning("daily_trade.score_failed", ticker=ticker, error=str(exc))
            return None

    def _pct_change(self, df: pd.DataFrame, days: int) -> float | None:
        if len(df) <= days:
            return None
        prev = float(df["Close"].iloc[-1 - days])
        curr = float(df["Close"].iloc[-1])
        if prev == 0:
            return None
        return (curr / prev - 1) * 100

    def _momentum_score(
        self,
        change_1d: float | None,
        change_5d: float | None,
        vol_spike: float,
        macd_hist: float,
    ) -> float:
        score = 0.0
        if change_1d is not None:
            if change_1d > 3:
                score += 35
            elif change_1d > 1:
                score += 25
            elif change_1d > 0:
                score += 15
            elif change_1d < -3:
                score -= 10
        if change_5d is not None:
            if change_5d > 8:
                score += 30
            elif change_5d > 3:
                score += 20
            elif change_5d > 0:
                score += 10
        if vol_spike >= 2.0:
            score += 25
        elif vol_spike >= 1.3:
            score += 15
        if macd_hist > 0:
            score += 10
        return min(max(score, 0), 100)

    def _technical_score(
        self,
        price: float,
        sma20: float,
        rsi: float | None,
        macd_hist: float,
    ) -> float:
        score = 0.0
        if price > sma20:
            score += 30
        if rsi is not None:
            if 45 <= rsi <= 68:
                score += 35
            elif 68 < rsi <= 75:
                score += 20
            elif rsi > 75:
                score += 5
            elif rsi < 35:
                score += 15
        if macd_hist > 0:
            score += 25
        return min(score, 100)

    def _classify_action(
        self,
        change_1d: float | None,
        change_5d: float | None,
        rsi: float | None,
        vol_spike: float,
    ) -> tuple[str, str]:
        if rsi is not None and rsi > 78:
            return _ACTION_WATCH, "Esperar pullback"
        if change_1d is not None and change_1d > 2 and vol_spike >= 1.5:
            return _ACTION_BUY, "1-3 días"
        if change_5d is not None and change_5d > 5:
            return _ACTION_SWING, "1-2 semanas"
        return _ACTION_SWING, "3-7 días"

    def _build_risks(
        self,
        rsi: float | None,
        vol_spike: float,
        change_5d: float | None,
    ) -> list[str]:
        risks: list[str] = ["Operación de corto plazo — usar stop-loss obligatorio."]
        if rsi is not None and rsi > 70:
            risks.append("RSI elevado: riesgo de corrección.")
        if vol_spike >= 3:
            risks.append("Volumen extremo: alta volatilidad.")
        if change_5d is not None and change_5d > 15:
            risks.append("Subida reciente fuerte: posible toma de ganancias.")
        return risks

    def _build_rationale(
        self,
        candidate: DiscoveryCandidate,
        change_1d: float | None,
        change_5d: float | None,
        vol_spike: float,
        rsi: float | None,
    ) -> str:
        parts = [candidate.rationale]
        if change_1d is not None:
            parts.append(f"Δ 1d: {change_1d:+.1f}%.")
        if change_5d is not None:
            parts.append(f"Δ 5d: {change_5d:+.1f}%.")
        if vol_spike >= 1.3:
            parts.append(f"Volumen {vol_spike:.1f}x promedio.")
        if rsi is not None:
            parts.append(f"RSI {rsi:.0f}.")
        return " ".join(parts)

    def _build_summary(
        self,
        picks: list[TradePick],
        regime: str,
        session: str,
        macro_mode: str = "neutral",
    ) -> str:
        session_es = {
            "pre_market": "pre-apertura",
            "mid_session": "media sesión",
            "post_market": "post-cierre",
        }.get(session, session)

        if not picks:
            return (
                f"Recomendaciones {session_es} ({regime}/{macro_mode}): no se encontraron setups "
                "de corto plazo con suficiente momentum, tendencia social y filtro de riesgo hoy."
            )

        top = picks[:3]
        leaders = ", ".join(
            f"{p.ticker} ({p.action}, +{p.expected_return_pct or '?'}% obj.)"
            for p in top
        )
        return (
            f"Recomendaciones {session_es} — régimen precio {regime}, macro {macro_mode}. "
            f"{len(picks)} oportunidades de corto plazo con filtros de riesgo. "
            f"Destacados: {leaders}."
        )
