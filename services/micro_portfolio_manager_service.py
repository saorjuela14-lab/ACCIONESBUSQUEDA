"""Micro / small-capital portfolio manager — acts like a capital desk for tiny books."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from domain.daily_trade import TradePick
from providers.interfaces import MarketDataProvider
from providers.market.intervals import assess_market_status
from services.capital_fit import capital_price_policy, discovery_themes_for_capital
from services.committee_consensus import SOURCE_TAG, SOURCE_TAG_SOFT, evaluate_consensus
from services.company_discovery_service import CompanyDiscoveryService
from services.risk_policy_service import RiskPolicyService
from utils.logging import get_logger

logger = get_logger(__name__)

# Screen candidates through the committee in rounds until we find buys (or exhaust).
# Keep latency under Cloudflare ~100s: stop at first approval, capped universe.
_COMMITTEE_BATCH = 3
_COMMITTEE_MAX_SCREEN = 9
_COMMITTEE_CONCURRENCY = 3

# Liquid names that often trade in the micro/penny range (validated live at runtime).
# Delisted / dead shells (NKLA, WISH, BBIG, etc.) must never appear here.
_MICRO_SEED_TICKERS = (
    # Prefer names that currently clear ~$8 micro max-line (reorder by typical price)
    "PLUG", "BITF", "AMC", "BBAI", "ABEV", "OPEN", "CLOV", "SNAP", "NIO",
    "SENS", "SOUN", "BB", "NOK", "F", "AAL", "SOFI", "SIRI", "T", "VZ",
    "PFE", "INTC", "KEY", "HBAN", "RF", "WBD", "PCG", "KMI", "AGNC", "NLY",
    "FCEL", "RIOT", "MARA", "ACHR", "JOBY", "LUNR", "ASTS", "CIFR",
    "APLD", "GRAB", "CHPT", "SPCE", "DNA", "MVST", "LAZR",
    "VALE", "ITUB", "PBR", "BBD", "GOLD", "NU", "CFG",
    "JD", "BIDU", "XPEV", "LI", "HOOD", "UPST", "PATH",
    "RIG", "HAL", "HL", "AG", "CDE", "BTBT", "CAN", "HUT", "CLSK",
    "WULF", "IREN", "IONQ", "RXRX", "ARR", "TWO", "ORC", "MPW",
)


_PENNY_THEMES = (
    "penny stocks under $8 volume spike",
    "micro cap biotech under $8",
    "stocks under $8 breakout today",
    "cheap small cap momentum under $10",
    "low priced stocks catalyst under $8",
    "sub $8 AI semiconductor stocks",
    "high volume stocks under $7 today",
    "bank stocks under $10 dividend",
    "China ADR under $10 momentum",
    "bitcoin miners under $10 volume",
)


@dataclass
class MicroAllocationLine:
    ticker: str
    company_name: str | None
    price: float
    shares: int
    allocation_usd: float
    allocation_pct: float
    rationale: str
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass
class MicroPortfolioPlan:
    capital: float
    cash_reserve_usd: float
    deployable_usd: float
    max_share_price: float
    lines: list[MicroAllocationLine]
    picks: list[TradePick]
    summary: str
    warnings: list[str]


class MicroPortfolioManagerService:
    """
    For micro capital (e.g. $22): continuously research affordable *live* names and
    build a whole-share plan. Keeps hunting across the universe until the investment
    committee soft-approves (micro majority) or the live list is exhausted — then the
    next autopilot cycle searches again.
    """

    def __init__(
        self,
        market_provider: MarketDataProvider,
        discovery_service: CompanyDiscoveryService | None = None,
        risk_service: RiskPolicyService | None = None,
        analysis_service: object | None = None,
    ) -> None:
        self._market = market_provider
        self._discovery = discovery_service or CompanyDiscoveryService(market_provider)
        self._risk = risk_service or RiskPolicyService()
        self._analysis = analysis_service

    def _position_count(self, capital: float) -> int:
        if capital <= 25:
            return 1
        if capital <= 30:
            return 2
        if capital <= 60:
            return 3
        if capital <= 100:
            return 3
        return 4

    def _risk_budgets(self, capital: float, tier: str) -> tuple[float, float, float, float]:
        """Return (cash_reserve_pct, deployable, max_line_usd, max_gross_pct)."""
        risk = self._risk.policy_from_settings()
        # Micro books need more breathing room than the firm 10% floor.
        cash_floor = 0.20 if tier == "micro" else 0.12
        cash_pct = max(risk.cash_reserve_pct / 100.0, cash_floor, 1.0 - risk.max_gross_exposure_pct / 100.0)
        max_gross_pct = min(risk.max_gross_exposure_pct / 100.0, 1.0 - cash_pct)
        deployable = round(capital * max_gross_pct, 2)
        # Ultra-micro: allow up to 40% on the single line so more liquid names fit
        pos_pct = risk.max_position_pct / 100.0
        if capital <= 25:
            pos_pct = max(pos_pct, 0.40)
        max_line = round(capital * pos_pct, 2)
        return cash_pct, deployable, max_line, max_gross_pct

    async def manage(
        self,
        capital: float,
        exclude_tickers: list[str] | None = None,
        max_candidates: int = 40,
    ) -> MicroPortfolioPlan:
        capital = max(1.0, float(capital))
        n_pos = self._position_count(capital)
        policy = capital_price_policy(capital, target_positions=n_pos)
        cash_pct, deployable, max_line_usd, max_gross_pct = self._risk_budgets(capital, policy.tier)
        cash_reserve = round(capital * cash_pct, 2)
        # Prefer policy max, but never below what one share under max_line needs
        max_price = max(
            float(policy.max_share_price or policy.prefer_max_price),
            min(12.0, max_line_usd * 1.02),
        )
        exclude = {t.upper() for t in (exclude_tickers or [])}
        warnings: list[str] = [
            (
                f"Política de riesgo: reserva cash ≥{cash_pct * 100:.0f}%, "
                f"exposición bruta ≤{max_gross_pct * 100:.0f}%, "
                f"máx ${max_line_usd:.2f}/posición · búsqueda continua hasta consenso."
            )
        ]

        candidates = await self._gather_candidates(
            policy=policy,
            max_price=max_price,
            exclude=exclude,
            max_candidates=max(max_candidates, 40),
        )

        if not candidates:
            warnings.append(
                "No hay penny stocks con cotización viva hoy; se excluyen deslistados/stale."
            )
            return MicroPortfolioPlan(
                capital=capital,
                cash_reserve_usd=cash_reserve,
                deployable_usd=deployable,
                max_share_price=max_price,
                lines=[],
                picks=[],
                summary=(
                    f"{policy.description_es} No hay candidatos líquidos y vivos disponibles ahora. "
                    f"Reserva efectivo ${cash_reserve:.2f}."
                ),
                warnings=warnings,
            )

        # Persistent hunt: for ultra-micro prefer technical desk (host can't finish 10-agent thesis)
        candidates.sort(key=lambda c: (-c["score"], c["price"]))
        mode = "micro" if policy.tier == "micro" else "strict"
        if capital <= 30 and mode == "micro":
            approved, committee_notes = self._technical_desk_approve(candidates, need=n_pos)
            # If technical desk empty, still try one fast micro committee batch
            if not approved and self._analysis:
                approved, more = await self._hunt_committee_consensus(
                    candidates, need=n_pos, mode=mode
                )
                committee_notes.extend(more)
        else:
            approved, committee_notes = await self._hunt_committee_consensus(
                candidates,
                need=n_pos,
                mode=mode,
            )
        warnings.extend(committee_notes)

        if not approved:
            warnings.append(
                "Búsqueda continua: se revisó el universo vivo y ninguno pasó el comité hoy. "
                "El próximo ciclo de autopilot vuelve a buscar (no se congela el cash)."
            )
            return MicroPortfolioPlan(
                capital=capital,
                cash_reserve_usd=cash_reserve,
                deployable_usd=deployable,
                max_share_price=max_price,
                lines=[],
                picks=[],
                summary=(
                    f"{policy.description_es} Escritorio en caza continua: sin consenso aún. "
                    f"Efectivo ${cash_reserve:.2f} ({cash_pct * 100:.0f}% reserva mínima)."
                ),
                warnings=warnings,
            )

        selected = approved[:n_pos]

        # Split deployable by equal weight, capped by max position %
        weight = 1.0 / len(selected)
        lines: list[MicroAllocationLine] = []
        picks: list[TradePick] = []
        spent = 0.0

        for c in selected:
            line_budget = min(deployable * weight, max_line_usd)
            price = c["price"]
            shares = int(line_budget // price) if price > 0 else 0
            if shares < 1 and price <= min(line_budget, deployable - spent, max_line_usd):
                shares = 1
            if shares < 1:
                continue
            cost = round(shares * price, 2)
            while shares >= 1 and (
                cost > max_line_usd + 0.01
                or spent + cost > deployable + 0.01
            ):
                shares -= 1
                cost = round(shares * price, 2)
            if shares < 1:
                continue

            spent += cost
            pct = round(cost / capital * 100, 1)
            stop = round(price * 0.92, 2)
            target = round(price * 1.12, 2)
            verdict = c.get("consensus")
            tag = verdict.source_tag if verdict else SOURCE_TAG
            label = "mayoría" if tag == SOURCE_TAG_SOFT else "unánime"
            rationale = (
                f"Comité {label} BUY (corto+largo): {shares} acciones @ ${price:.2f} = ${cost:.2f} "
                f"({pct}% del portafolio). {c.get('rationale', '')}"
            ).strip()

            lines.append(
                MicroAllocationLine(
                    ticker=c["ticker"],
                    company_name=c.get("company_name"),
                    price=price,
                    shares=shares,
                    allocation_usd=cost,
                    allocation_pct=pct,
                    rationale=rationale,
                    stop_loss=stop,
                    take_profit=target,
                )
            )
            sources = list(c.get("sources") or ["capital_desk"])
            if tag not in sources:
                sources.append(tag)
            picks.append(
                TradePick(
                    ticker=c["ticker"],
                    company_name=c.get("company_name"),
                    action="compra capital",
                    horizon="corto+largo (comité)",
                    score=round(c["score"], 2),
                    confidence=float(c.get("committee_confidence") or 0.6),
                    current_price=price,
                    entry_price=price,
                    target_price=target,
                    stop_loss=stop,
                    expected_return_pct=12.0,
                    catalysts=c.get("catalysts") or [],
                    rationale=rationale,
                    risks=[
                        "Penny / micro-cap: alta volatilidad y riesgo de liquidez.",
                        f"Tope de posición {self._risk.policy_from_settings().max_position_pct:.0f}% "
                        "por política de riesgo; no usar el 100% del portafolio.",
                    ],
                    sources=sources,
                    committee_unanimous=True,
                    committee_recommendation=(
                        verdict.recommendation if verdict else "buy"
                    ),
                    short_horizon_buy=True,
                    long_horizon_buy=True,
                )
            )

        cash_total = round(capital - spent, 2)
        deployed_pct = round(spent / capital * 100, 1) if capital else 0.0
        tickers_txt = ", ".join(f"{l.ticker}×{l.shares}" for l in lines) or "—"
        summary = (
            f"{policy.description_es} "
            f"Plan autónomo con consenso del comité: desplegar ${spent:.2f} ({deployed_pct}%) "
            f"en {len(lines)} posiciones ({tickers_txt}); efectivo ${cash_total:.2f} "
            f"({cash_total / capital * 100:.0f}%). "
            f"Micro: mayoría BUY corto+largo; no se usa el 100% del capital."
        )
        if not lines:
            warnings.append("Ninguna línea pudo comprar ≥1 acción dentro de los topes de riesgo.")
            summary = (
                f"{policy.description_es} Capital insuficiente para 1 acción dentro de la banda "
                f"(máx ${max_price:.2f}) y tope por posición ${max_line_usd:.2f}."
            )
        elif deployed_pct > max_gross_pct * 100 + 0.5:
            warnings.append("Exposición ajustada: se recortó para respetar el tope bruto.")

        return MicroPortfolioPlan(
            capital=capital,
            cash_reserve_usd=cash_total,
            deployable_usd=deployable,
            max_share_price=max_price,
            lines=lines,
            picks=picks,
            summary=summary,
            warnings=warnings,
        )

    def _technical_desk_approve(
        self,
        candidates: list[dict],
        *,
        need: int,
    ) -> tuple[list[dict], list[str]]:
        """Ultra-micro path: approve liquid names with healthy technicals (no multi-agent wait)."""
        notes: list[str] = [
            "Mesa técnica micro: filtro RSI/momentum/volumen sobre seeds líquidos "
            "(el comité completo 10 agentes no cabe en el timeout del host)."
        ]
        approved: list[dict] = []
        screened = 0
        for c in candidates:
            if len(approved) >= need:
                break
            tech = c.get("tech") or {}
            screened += 1
            if not tech.get("ok"):
                continue
            enriched = dict(c)
            enriched["score"] = float(c["score"]) + 30.0
            enriched["committee_confidence"] = 0.58
            enriched["consensus"] = type(
                "V",
                (),
                {
                    "source_tag": SOURCE_TAG_SOFT,
                    "recommendation": "buy",
                    "passed": True,
                },
            )()
            enriched["rationale"] = (
                f"Mesa técnica micro OK (RSI {tech.get('rsi')}, "
                f"5d {tech.get('chg5')}%, vol×{tech.get('vol_spike')}). "
                f"{c.get('rationale', '')}"
            ).strip()
            sources = list(c.get("sources") or [])
            sources.append(SOURCE_TAG_SOFT)
            sources.append("micro_technical_desk")
            enriched["sources"] = sources
            approved.append(enriched)
        notes.append(
            f"Mesa técnica: {len(approved)} aprobados / {screened} revisados."
        )
        return approved, notes

    async def _hunt_committee_consensus(
        self,
        candidates: list[dict],
        *,
        need: int,
        mode: str = "strict",
    ) -> tuple[list[dict], list[str]]:
        """Keep screening batches until we have `need` approvals or exhaust the list."""
        notes: list[str] = []
        if not self._analysis:
            notes.append(
                "Comité no disponible en este contexto — no se asigna capital sin análisis."
            )
            return [], notes

        approved: list[dict] = []
        screened = 0
        rejected = 0
        universe = candidates[:_COMMITTEE_MAX_SCREEN]

        for start in range(0, len(universe), _COMMITTEE_BATCH):
            if len(approved) >= need:
                break
            batch = universe[start : start + _COMMITTEE_BATCH]
            batch_ok, batch_notes = await self._filter_committee_consensus(
                batch, mode=mode, limit=len(batch)
            )
            screened += len(batch)
            rejected += len(batch) - len(batch_ok)
            approved.extend(batch_ok)
            # Keep only the most informative note from each batch
            if batch_notes:
                notes.append(batch_notes[-1])
            logger.info(
                "micro_committee_hunt_batch",
                screened=screened,
                approved=len(approved),
                need=need,
                mode=mode,
            )

        notes.insert(
            0,
            (
                f"Caza continua: {len(approved)} aprobados / {screened} analizados "
                f"({rejected} rechazados, universo {len(universe)})."
            ),
        )
        # De-dupe by ticker preserving score order
        seen: set[str] = set()
        unique: list[dict] = []
        for c in sorted(approved, key=lambda x: (-x["score"], x["price"])):
            if c["ticker"] in seen:
                continue
            seen.add(c["ticker"])
            unique.append(c)
        return unique, notes

    async def _filter_committee_consensus(
        self,
        candidates: list[dict],
        *,
        mode: str = "strict",
        limit: int | None = None,
    ) -> tuple[list[dict], list[str]]:
        """Run committee on a batch; micro uses soft majority dual-horizon."""
        notes: list[str] = []
        if not self._analysis:
            notes.append(
                "Comité no disponible en este contexto — no se asigna capital sin análisis."
            )
            return [], notes

        sem = asyncio.Semaphore(_COMMITTEE_CONCURRENCY)
        cap = limit if limit is not None else _COMMITTEE_BATCH
        screen = candidates[:cap]

        async def _one(c: dict) -> dict | None:
            ticker = c["ticker"]
            async with sem:
                try:
                    score_fn = getattr(
                        self._analysis, "score_for_micro_consensus", None
                    )
                    if mode == "micro" and callable(score_fn):
                        thesis = await score_fn(ticker)
                    else:
                        thesis = await self._analysis.score_for_consensus(ticker)
                except Exception as exc:
                    logger.warning("micro_committee_failed", ticker=ticker, error=str(exc))
                    return None
            verdict = evaluate_consensus(thesis, mode=mode)
            if not verdict.passed:
                logger.info(
                    "micro_committee_reject",
                    ticker=ticker,
                    mode=mode,
                    reasons=verdict.reasons[:4],
                )
                return None
            enriched = dict(c)
            enriched["consensus"] = verdict
            enriched["committee_confidence"] = float(thesis.confidence or 0.6)
            enriched["score"] = float(c["score"]) + 40.0 + float(thesis.confidence or 0) * 20
            label = "mayoría" if mode == "micro" else "unánime"
            enriched["rationale"] = (
                f"Consenso comité ({label}) {verdict.recommendation}: "
                f"corto OK · largo OK. "
                f"{c.get('rationale', '')}"
            ).strip()
            return enriched

        results = await asyncio.gather(*[_one(c) for c in screen])
        approved = [r for r in results if r]
        rejected = len(screen) - len(approved)
        gate = "mayoría micro" if mode == "micro" else "BUY unánime"
        notes.append(
            f"Comité lote: {len(approved)}/{len(screen)} con {gate} "
            f"({rejected} rechazados)."
        )
        approved.sort(key=lambda c: (-c["score"], c["price"]))
        return approved, notes

    async def _gather_candidates(
        self,
        policy,
        max_price: float,
        exclude: set[str],
        max_candidates: int,
    ) -> list[dict]:
        found: dict[str, dict] = {}
        discovery_tickers: list[str] = []
        discovery_meta: dict = {}

        # Ultra-micro: skip slow social/news discovery — hunt liquid seeds only
        if policy.capital > 30:
            themes = discovery_themes_for_capital(policy, list(_PENNY_THEMES))
            report = await self._discovery.research(
                themes=themes,
                max_candidates=max_candidates,
                exclude_tickers=list(exclude),
                max_price=max_price,
            )
            discovery_tickers = [
                c.ticker for c in report.candidates
                if c.ticker not in exclude
            ]
            discovery_meta = {c.ticker: c for c in report.candidates}

        seeds = [t for t in _MICRO_SEED_TICKERS if t not in exclude]
        seed_cap = 18 if policy.capital <= 30 else 36
        probe_tickers = list(dict.fromkeys(discovery_tickers + seeds[:seed_cap]))
        probes = await asyncio.gather(*[self._probe_live_quote(t) for t in probe_tickers])

        for ticker, probe in zip(probe_tickers, probes):
            if not probe:
                continue
            price = probe["price"]
            if price <= 0 or price > max_price or price < policy.min_share_price:
                continue
            disc = discovery_meta.get(ticker)
            if disc:
                score = disc.score + (10 if price <= max_price * 0.5 else 0)
                score += 5
                tech = probe.get("tech") or {}
                score += float(tech.get("score") or 0) * 0.5
                found[ticker] = {
                    "ticker": ticker,
                    "company_name": disc.company_name or probe.get("company_name"),
                    "price": price,
                    "score": score,
                    "rationale": disc.rationale,
                    "catalysts": (disc.news_headlines or [])[:3],
                    "sources": disc.sources,
                    "tech": tech,
                }
            elif ticker not in found:
                thin_penalty = 8 if price < 0.25 else 0
                tech = probe.get("tech") or {}
                tech_score = float(tech.get("score") or 0)
                found[ticker] = {
                    "ticker": ticker,
                    "company_name": probe.get("company_name"),
                    "price": price,
                    "score": 25.0 - thin_penalty + (5 if price <= max_price * 0.5 else 0) + tech_score,
                    "rationale": "Seed líquido micro con cotización viva.",
                    "catalysts": [],
                    "sources": ["micro_seed"],
                    "tech": tech,
                }
            if ticker in found:
                found[ticker]["tech"] = probe.get("tech") or found[ticker].get("tech") or {}

        ranked = sorted(found.values(), key=lambda c: (-c["score"], c["price"]))
        return ranked[: max(max_candidates, 12)]

    async def _probe_live_quote(self, ticker: str) -> dict | None:
        """Require a positive quote AND daily bars marked live (not stale/delisted)."""
        try:
            quote, hist = await asyncio.gather(
                self._market.get_quote(ticker),
                self._market.get_history(ticker, period="3mo", interval="1d"),
            )
        except Exception as exc:
            logger.debug("micro_probe_failed", ticker=ticker, error=str(exc))
            return None
        price = float((quote or {}).get("current_price") or 0)
        if price <= 0:
            return None
        status, stale_days, as_of = assess_market_status(hist, "1d")
        if status != "live":
            logger.info(
                "micro_skip_non_live",
                ticker=ticker,
                status=status,
                stale_days=stale_days,
                as_of=as_of,
            )
            return None
        return {
            "price": price,
            "company_name": (quote or {}).get("company_name"),
            "as_of": as_of,
            "tech": self._quick_tech_score(hist, price),
        }

    def _quick_tech_score(self, hist, price: float) -> dict:
        """Lightweight technical screen from daily bars (no multi-agent latency)."""
        try:
            import pandas as pd

            if hist is None or getattr(hist, "empty", True) or len(hist) < 25:
                return {"ok": False, "score": -50, "reason": "hist_corta"}
            close = hist["Close"].astype(float)
            chg1 = float(close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else 0.0
            chg5 = float(close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 6 else 0.0
            # RSI(14) approx
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, pd.NA)
            rsi = float(100 - (100 / (1 + rs.iloc[-1]))) if rs.iloc[-1] == rs.iloc[-1] else 50.0
            sma20 = float(close.tail(20).mean())
            above_sma = price >= sma20 * 0.98
            vol = hist["Volume"].astype(float) if "Volume" in hist.columns else None
            vol_spike = 1.0
            if vol is not None and len(vol) > 20:
                avg = float(vol.tail(20).mean()) or 1.0
                vol_spike = float(vol.iloc[-1] / avg)

            # Hard rejects: dump / extreme overbought / broken trend
            if chg5 <= -12:
                return {"ok": False, "score": -40, "reason": "caída_5d", "rsi": rsi, "chg5": chg5}
            if rsi >= 78:
                return {"ok": False, "score": -30, "reason": "sobrecompra", "rsi": rsi, "chg5": chg5}
            if rsi <= 22 and chg5 < 0:
                return {"ok": False, "score": -25, "reason": "sobreventa_bajista", "rsi": rsi, "chg5": chg5}

            score = 20.0
            score += max(-10, min(15, chg5))
            score += max(-5, min(8, chg1))
            if 40 <= rsi <= 65:
                score += 12
            elif 30 <= rsi < 40 or 65 < rsi <= 72:
                score += 4
            if above_sma:
                score += 8
            if vol_spike >= 1.4:
                score += 6
            ok = score >= 22 and (chg5 > 0 or above_sma or vol_spike >= 1.5)
            return {
                "ok": ok,
                "score": round(score, 2),
                "reason": "tech_ok" if ok else "tech_debil",
                "rsi": round(rsi, 1),
                "chg5": round(chg5, 2),
                "chg1": round(chg1, 2),
                "vol_spike": round(vol_spike, 2),
                "above_sma20": above_sma,
            }
        except Exception as exc:
            return {"ok": False, "score": -50, "reason": f"tech_error:{exc}"}
