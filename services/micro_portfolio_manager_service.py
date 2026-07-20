"""Micro / small-capital portfolio manager — acts like a capital desk for tiny books."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from domain.daily_trade import TradePick
from domain.discovery import DiscoveryCandidate
from providers.interfaces import MarketDataProvider
from services.capital_fit import capital_price_policy, discovery_themes_for_capital
from services.company_discovery_service import CompanyDiscoveryService
from utils.logging import get_logger

logger = get_logger(__name__)

# Liquid names that often trade in the micro/penny range (validated by quote at runtime).
_MICRO_SEED_TICKERS = (
    "SOUN", "PLUG", "FCEL", "NKLA", "RIOT", "MARA", "OPEN", "CLOV",
    "WISH", "BBIG", "SENS", "JNUG", "FARE", "SIRI", "NOK", "SNAP",
    "F", "AAL", "UAL", "SOFI", "PLTR", "NIO", "LCID", "RIVN",
    "AMC", "GME", "BB", "DNA", "ACHR", "JOBY", "LUNR", "ASTS",
)

_PENNY_THEMES = (
    "penny stocks under $5 volume spike",
    "micro cap biotech under $5",
    "stocks under $3 breakout today",
    "cheap small cap momentum under $5",
    "OTC and low priced stocks catalyst",
    "sub $5 AI semiconductor stocks",
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
    For micro capital (e.g. $22): research affordable names and build a whole-share plan.
    Never returns empty if at least one valid quote fits the band.
    """

    def __init__(
        self,
        market_provider: MarketDataProvider,
        discovery_service: CompanyDiscoveryService | None = None,
    ) -> None:
        self._market = market_provider
        self._discovery = discovery_service or CompanyDiscoveryService(market_provider)

    def _position_count(self, capital: float) -> int:
        if capital <= 30:
            return 2
        if capital <= 60:
            return 3
        if capital <= 100:
            return 3
        return 4

    async def manage(
        self,
        capital: float,
        exclude_tickers: list[str] | None = None,
        max_candidates: int = 20,
    ) -> MicroPortfolioPlan:
        capital = max(1.0, float(capital))
        n_pos = self._position_count(capital)
        policy = capital_price_policy(capital, target_positions=n_pos)
        cash_pct = 0.10 if policy.tier == "micro" else 0.12
        cash_reserve = round(capital * cash_pct, 2)
        deployable = round(capital - cash_reserve, 2)
        max_price = policy.max_share_price or policy.prefer_max_price
        exclude = {t.upper() for t in (exclude_tickers or [])}
        warnings: list[str] = []

        candidates = await self._gather_candidates(
            policy=policy,
            max_price=max_price,
            exclude=exclude,
            max_candidates=max_candidates,
        )

        if not candidates:
            warnings.append("No se validaron penny stocks líquidos hoy; reintenta más tarde.")
            return MicroPortfolioPlan(
                capital=capital,
                cash_reserve_usd=cash_reserve,
                deployable_usd=deployable,
                max_share_price=max_price,
                lines=[],
                picks=[],
                summary=(
                    f"{policy.description_es} No hay candidatos asequibles disponibles ahora. "
                    f"Reserva efectivo ${cash_reserve:.2f}."
                ),
                warnings=warnings,
            )

        # Rank: cheapest-fit first among liquid names with buzz/liquidity proxy
        candidates.sort(key=lambda c: (-c["score"], c["price"]))
        selected = candidates[:n_pos]

        # Split deployable by equal weight (capital desk: simple, whole shares)
        weight = 1.0 / len(selected)
        lines: list[MicroAllocationLine] = []
        picks: list[TradePick] = []
        spent = 0.0

        for c in selected:
            line_budget = deployable * weight
            price = c["price"]
            shares = int(line_budget // price) if price > 0 else 0
            if shares < 1 and price <= deployable - spent:
                shares = 1
            if shares < 1:
                continue
            cost = round(shares * price, 2)
            if spent + cost > deployable + 0.01:
                shares = int((deployable - spent) // price)
                if shares < 1:
                    continue
                cost = round(shares * price, 2)

            spent += cost
            pct = round(cost / capital * 100, 1)
            stop = round(price * 0.92, 2)
            target = round(price * 1.12, 2)
            rationale = (
                f"Gestión capital micro: {shares} acciones @ ${price:.2f} = ${cost:.2f} "
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
            picks.append(
                TradePick(
                    ticker=c["ticker"],
                    company_name=c.get("company_name"),
                    action="compra capital",
                    horizon="gestión 1-4 semanas",
                    score=round(c["score"], 2),
                    confidence=0.55,
                    current_price=price,
                    entry_price=price,
                    target_price=target,
                    stop_loss=stop,
                    expected_return_pct=12.0,
                    catalysts=c.get("catalysts") or [],
                    rationale=rationale,
                    risks=[
                        "Penny / micro-cap: alta volatilidad y riesgo de liquidez.",
                        "Usar stop-loss; no concentrar más del 50% en un solo nombre.",
                    ],
                    sources=c.get("sources") or ["capital_desk"],
                )
            )

        leftover = round(deployable - spent, 2)
        cash_total = round(cash_reserve + max(0, leftover), 2)
        tickers_txt = ", ".join(f"{l.ticker}×{l.shares}" for l in lines) or "—"
        summary = (
            f"{policy.description_es} "
            f"Plan de gestión: desplegar ${spent:.2f} en {len(lines)} posiciones "
            f"({tickers_txt}); efectivo ${cash_total:.2f} "
            f"({cash_total / capital * 100:.0f}%). "
            f"Proporciones orientadas a acciones enteras dentro de tu capital."
        )
        if not lines:
            warnings.append("Ninguna línea pudo comprar ≥1 acción con el capital disponible.")
            summary = (
                f"{policy.description_es} Capital insuficiente para 1 acción en los nombres "
                f"encontrados (máx ${max_price:.2f}). Considera aportar más capital o esperar un dip."
            )

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

    async def _gather_candidates(
        self,
        policy,
        max_price: float,
        exclude: set[str],
        max_candidates: int,
    ) -> list[dict]:
        themes = discovery_themes_for_capital(policy, list(_PENNY_THEMES))
        report = await self._discovery.research(
            themes=themes,
            max_candidates=max_candidates,
            exclude_tickers=list(exclude),
            max_price=max_price,
        )

        found: dict[str, dict] = {}
        for c in report.candidates:
            price = await self._safe_price(c.ticker)
            if price is None or price > max_price or price < policy.min_share_price:
                continue
            if c.ticker in exclude:
                continue
            found[c.ticker] = {
                "ticker": c.ticker,
                "company_name": c.company_name,
                "price": price,
                "score": c.score + (10 if price <= max_price * 0.5 else 0),
                "rationale": c.rationale,
                "catalysts": c.news_headlines[:3],
                "sources": c.sources,
            }

        # Seed universe fill — guarantee options for capital desk
        seeds = [t for t in _MICRO_SEED_TICKERS if t not in found and t not in exclude]
        seed_quotes = await asyncio.gather(*[self._safe_quote(t) for t in seeds[:24]])
        for ticker, quote in zip(seeds[:24], seed_quotes):
            if not quote:
                continue
            price = float(quote.get("current_price") or 0)
            if price <= 0 or price > max_price or price < policy.min_share_price:
                continue
            found[ticker] = {
                "ticker": ticker,
                "company_name": quote.get("company_name"),
                "price": price,
                "score": 25 + (15 if price <= 2 else 5),
                "rationale": "Candidato líquido en banda de precio para capital micro.",
                "catalysts": [],
                "sources": ["seed_universe"],
            }

        return list(found.values())

    async def _safe_price(self, ticker: str) -> float | None:
        try:
            q = await self._market.get_quote(ticker)
            p = float(q.get("current_price") or 0)
            return p if p > 0 else None
        except Exception:
            return None

    async def _safe_quote(self, ticker: str) -> dict | None:
        try:
            return await self._market.get_quote(ticker)
        except Exception:
            return None
