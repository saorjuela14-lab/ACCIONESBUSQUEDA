"""Market-aware capital allocation advisor — % buckets for emerging and core names."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from domain.allocation_plan import AllocationBucket, MarketAllocationPlan, TickerAllocationItem
from domain.entities import InvestmentMemoryRecord, WatchlistItem
from domain.enums import InvestmentRecommendation
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)

_BUY = {InvestmentRecommendation.BUY.value, InvestmentRecommendation.STRONG_BUY.value, "buy", "strong_buy"}
_EMERGING_SECTORS = {
    "Biotechnology",
    "Drug Manufacturers—General",
    "Drug Manufacturers—Specialty & Generic",
    "Aerospace & Defense",
    "Semiconductors",
    "Software—Infrastructure",
    "Software—Application",
    "Solar",
    "Utilities—Renewable",
}
_EMERGING_MCAP_MAX = 25e9

_STYLE_WEIGHTS: dict[str, dict[str, float]] = {
    "emerging_focused": {
        "cash": 10,
        "emerging": 45,
        "core": 20,
        "momentum": 25,
    },
    "balanced": {
        "cash": 15,
        "emerging": 30,
        "core": 35,
        "momentum": 20,
    },
    "defensive": {
        "cash": 25,
        "emerging": 15,
        "core": 45,
        "momentum": 15,
    },
}

_REGIME_ADJ: dict[str, dict[str, float]] = {
    "bullish": {"emerging": 5, "momentum": 5, "cash": -10},
    "bearish": {"emerging": -5, "momentum": -5, "cash": 10},
    "neutral": {},
}

_BUCKET_LABELS = {
    "cash": "Reserva en efectivo",
    "emerging": "Empresas emergentes / alto crecimiento",
    "core": "Núcleo estabilizador",
    "momentum": "Alpha / momentum",
}


@dataclass
class _ScoredTicker:
    ticker: str
    company_name: str | None
    score: float
    confidence: float
    recommendation: str
    is_emerging: bool
    category: str
    rationale: str
    price: float | None


class MarketAllocationAdvisorService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    def _is_emerging(self, quote: dict) -> bool:
        mcap = float(quote.get("market_cap") or 0)
        sector = quote.get("sector") or ""
        industry = quote.get("industry") or ""
        if 0 < mcap <= _EMERGING_MCAP_MAX:
            return True
        if sector in _EMERGING_SECTORS or any(s in industry for s in _EMERGING_SECTORS):
            return True
        growth = quote.get("revenueGrowth") or quote.get("earningsGrowth")
        if growth and float(growth) > 0.25:
            return True
        return False

    def _composite_score(self, memory: InvestmentMemoryRecord | None, change_pct: float | None) -> tuple[float, float, str]:
        if not memory:
            return 0.0, 0.3, "hold"
        scores = memory.scores or {}
        agg = sum(scores.values()) / len(scores) if scores else 0.0
        rec = (memory.recommendation or "hold").lower()
        conf = memory.confidence or 0.5
        if rec in _BUY:
            agg += 15
        elif rec in ("sell", "strong_sell"):
            agg -= 20
        if change_pct is not None:
            agg += max(-10, min(10, change_pct))
        return agg, conf, rec

    def _assign_category(self, scored: _ScoredTicker) -> str:
        if scored.recommendation in _BUY and scored.score >= 20 and scored.confidence >= 0.6:
            return "momentum"
        if scored.is_emerging and scored.score >= 0:
            return "emerging"
        if scored.score >= 5 or scored.recommendation in ("hold", "buy", "strong_buy"):
            return "core"
        if scored.is_emerging:
            return "emerging"
        return "core"

    def _bucket_weights(self, regime: str, style: str) -> dict[str, float]:
        base = dict(_STYLE_WEIGHTS.get(style, _STYLE_WEIGHTS["balanced"]))
        adj = _REGIME_ADJ.get(regime, {})
        for k, v in adj.items():
            base[k] = base.get(k, 0) + v
        base["cash"] = max(5, min(40, base.get("cash", 15)))
        total = sum(base.values())
        return {k: round(v / total * 100, 1) for k, v in base.items()}

    async def _score_ticker(
        self,
        item: WatchlistItem,
        memory: InvestmentMemoryRecord | None,
    ) -> _ScoredTicker | None:
        try:
            quote, hist = await asyncio.gather(
                self._market.get_quote(item.ticker),
                self._market.get_history(item.ticker, period="5d", interval="1d"),
            )
            change_pct = None
            if not hist.empty and len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                last = float(hist["Close"].iloc[-1])
                if prev:
                    change_pct = round((last - prev) / prev * 100, 2)

            score, conf, rec = self._composite_score(memory, change_pct)
            emerging = self._is_emerging(quote)
            name = quote.get("company_name") or item.company_name
            rationale_parts = []
            if emerging:
                rationale_parts.append("perfil emergente/mediana capitalización")
            if rec in _BUY:
                rationale_parts.append(f"recomendación {rec.upper()}")
            if memory and memory.thesis:
                rationale_parts.append(memory.thesis[:80])
            elif memory and memory.expected_outcome:
                rationale_parts.append(memory.expected_outcome[:80])

            scored = _ScoredTicker(
                ticker=item.ticker.upper(),
                company_name=name,
                score=score,
                confidence=conf,
                recommendation=rec,
                is_emerging=emerging,
                category="core",
                rationale="; ".join(rationale_parts) or "En watchlist activa",
                price=float(quote.get("current_price") or 0) or None,
            )
            scored.category = self._assign_category(scored)
            return scored
        except Exception as exc:
            logger.warning("allocation.score.failed", ticker=item.ticker, error=str(exc))
            return None

    async def advise(
        self,
        capital: float,
        watchlist: list[WatchlistItem],
        memory_by_ticker: dict[str, InvestmentMemoryRecord],
        market_regime: str = "neutral",
        market_regime_score: float = 0.0,
        strategy_style: str = "balanced",
        strong_sectors: list[str] | None = None,
    ) -> MarketAllocationPlan:
        capital = max(1.0, capital)
        regime = market_regime.lower() if market_regime else "neutral"
        weights = self._bucket_weights(regime, strategy_style)

        if not watchlist:
            return MarketAllocationPlan(
                capital=capital,
                market_regime=regime,
                market_regime_score=market_regime_score,
                strategy_style=strategy_style,
                market_view="Sin tickers en watchlist para construir asignación.",
                summary="Agrega tickers a la watchlist y vuelve a generar.",
                cash_reserve_pct=weights.get("cash", 15),
                warnings=["Watchlist vacía"],
            )

        scored_list = await asyncio.gather(
            *[self._score_ticker(item, memory_by_ticker.get(item.ticker.upper())) for item in watchlist]
        )
        scored = [s for s in scored_list if s is not None]
        excluded = [item.ticker for item, s in zip(watchlist, scored_list) if s is None]

        by_cat: dict[str, list[_ScoredTicker]] = {"emerging": [], "core": [], "momentum": []}
        for s in scored:
            if s.recommendation in ("sell", "strong_sell") and s.score < -5:
                excluded.append(s.ticker)
                continue
            by_cat.setdefault(s.category, []).append(s)

        for cat in by_cat:
            by_cat[cat].sort(key=lambda x: x.score * x.confidence, reverse=True)

        regime_es = {"bullish": "alcista", "bearish": "bajista", "neutral": "neutral"}.get(regime, regime)
        style_es = {
            "emerging_focused": "enfoque emergentes",
            "balanced": "balanceada",
            "defensive": "defensiva",
        }.get(strategy_style, strategy_style)
        sector_hint = f" Sectores fuertes: {', '.join(strong_sectors[:3])}." if strong_sectors else ""
        market_view = (
            f"Mercado {regime_es} (score {market_regime_score:+.1f}). "
            f"Estrategia {style_es}.{sector_hint} "
            f"Se analizaron {len(scored)} tickers de la watchlist."
        )

        buckets: list[AllocationBucket] = []
        items: list[TickerAllocationItem] = []

        for key in ("cash", "emerging", "core", "momentum"):
            pct = weights.get(key, 0)
            usd = round(capital * pct / 100, 2)
            if key == "cash":
                buckets.append(
                    AllocationBucket(
                        key=key,
                        label=_BUCKET_LABELS[key],
                        allocation_pct=pct,
                        allocation_usd=usd,
                        tickers=[],
                        description="Colchón de liquidez para oportunidades y gestión de riesgo.",
                    )
                )
                continue

            candidates = by_cat.get(key, [])
            if not candidates and key == "momentum":
                candidates = [s for s in scored if s.recommendation in _BUY][:3]
            if not candidates and key == "emerging":
                candidates = [s for s in scored if s.is_emerging][:4]
            if not candidates:
                candidates = scored[:3]

            tickers_in = [c.ticker for c in candidates[:5]]
            desc_parts = []
            if key == "emerging":
                desc_parts.append("Biotech, quantum, espacio y mediana cap con perfil de crecimiento")
            elif key == "core":
                desc_parts.append("Posiciones más estables para anclar el portafolio")
            else:
                desc_parts.append("Mayor convicción según scores del comité")

            buckets.append(
                AllocationBucket(
                    key=key,
                    label=_BUCKET_LABELS[key],
                    allocation_pct=pct,
                    allocation_usd=usd,
                    tickers=tickers_in,
                    description=" — ".join(desc_parts) + f": {', '.join(tickers_in) or '—'}",
                )
            )

            if not candidates:
                continue

            total_cat_score = sum(max(0.1, c.score * c.confidence) for c in candidates[:5])
            for c in candidates[:5]:
                share = max(0.1, c.score * c.confidence) / total_cat_score
                line_pct = round(pct * share, 1)
                line_usd = round(capital * line_pct / 100, 2)
                items.append(
                    TickerAllocationItem(
                        ticker=c.ticker,
                        company_name=c.company_name,
                        bucket=key,
                        allocation_pct=line_pct,
                        allocation_usd=line_usd,
                        recommendation=c.recommendation.upper(),
                        confidence=round(c.confidence, 2),
                        score=round(c.score, 1),
                        rationale=c.rationale,
                        is_emerging=c.is_emerging,
                    )
                )

        lines_summary = []
        for b in buckets:
            if b.key == "cash":
                lines_summary.append(f"{b.allocation_pct:.0f}% (${b.allocation_usd:,.0f}) en {b.label.lower()}")
            else:
                lines_summary.append(
                    f"{b.allocation_pct:.0f}% (${b.allocation_usd:,.0f}) en {b.label.lower()}"
                    + (f" → {', '.join(b.tickers)}" if b.tickers else "")
                )

        summary = (
            f"Con ${capital:,.0f} de capital, el comité sugiere: "
            + "; ".join(lines_summary)
            + "."
        )

        warnings: list[str] = []
        if len(scored) < 3:
            warnings.append("Pocos tickers analizados — diversificación limitada.")
        if not any(s.is_emerging for s in scored):
            warnings.append("No se detectaron emergentes claros; revisa biotech/mid-cap en watchlist.")

        return MarketAllocationPlan(
            capital=capital,
            market_regime=regime,
            market_regime_score=market_regime_score,
            strategy_style=strategy_style,
            market_view=market_view,
            summary=summary,
            cash_reserve_pct=weights.get("cash", 15),
            buckets=buckets,
            items=items,
            excluded_tickers=excluded,
            warnings=warnings,
        )
