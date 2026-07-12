"""Daily short-term trade recommendations from trends + momentum."""

import asyncio
from datetime import date, datetime, timezone

import pandas as pd

from agents.technical.indicators import build_trade_levels, enrich_indicators
from database.repositories.daily_trade_repository import DailyTradeRepository
from domain.daily_trade import DailyTradeReport, TradePick
from domain.discovery import DiscoveryCandidate
from providers.interfaces import MarketDataProvider
from services.company_discovery_service import CompanyDiscoveryService
from services.market_dashboard_service import MarketDashboardService
from utils.logging import get_logger

logger = get_logger(__name__)

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
    ) -> None:
        self._market = market_provider
        self._discovery = discovery_service
        self._repo = trade_repo
        self._dashboard = MarketDashboardService()

    async def generate(
        self,
        session: str = "pre_market",
        max_picks: int = 8,
        exclude_tickers: list[str] | None = None,
        persist: bool = True,
    ) -> DailyTradeReport:
        logger.info("daily_trade.generate.start", session=session)

        regime = await self._fetch_market_regime()
        discovery = await self._discovery.research(
            themes=list(_SHORT_TERM_THEMES),
            max_candidates=25,
            exclude_tickers=exclude_tickers or [],
        )

        scored: list[TradePick] = []
        for candidate in discovery.candidates[:20]:
            pick = await self._score_candidate(candidate)
            if pick:
                scored.append(pick)

        scored.sort(key=lambda p: p.score, reverse=True)
        picks = scored[:max_picks]

        summary = self._build_summary(picks, regime, session)
        report = DailyTradeReport(
            report_date=date.today(),
            generated_at=datetime.now(timezone.utc),
            session=session,
            market_regime=regime,
            summary=summary,
            picks=picks,
        )

        if persist and self._repo:
            await self._repo.save(report)

        logger.info("daily_trade.generate.done", picks=len(picks), session=session)
        return report

    async def get_latest(self) -> DailyTradeReport | None:
        if not self._repo:
            return None
        return await self._repo.get_latest()

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

    async def _score_candidate(self, candidate: DiscoveryCandidate) -> TradePick | None:
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

            if total < 35:
                return None

            action, horizon = self._classify_action(change_1d, change_5d, rsi, vol_spike)
            if action == _ACTION_WATCH and total < 50:
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

    def _build_summary(self, picks: list[TradePick], regime: str, session: str) -> str:
        session_es = {
            "pre_market": "pre-apertura",
            "mid_session": "media sesión",
            "post_market": "post-cierre",
        }.get(session, session)

        if not picks:
            return (
                f"Recomendaciones {session_es} ({regime}): no se encontraron setups de corto plazo "
                "con suficiente momentum y tendencia social hoy."
            )

        top = picks[:3]
        leaders = ", ".join(
            f"{p.ticker} ({p.action}, +{p.expected_return_pct or '?'}% obj.)"
            for p in top
        )
        return (
            f"Recomendaciones {session_es} — régimen {regime}. "
            f"{len(picks)} oportunidades de corto plazo detectadas por tendencias sociales, "
            f"noticias y momentum técnico. Destacados: {leaders}."
        )
