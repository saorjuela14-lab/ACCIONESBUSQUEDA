"""Micro / small-capital portfolio manager — acts like a capital desk for tiny books."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from domain.daily_trade import TradePick
from providers.interfaces import MarketDataProvider
from providers.market.intervals import assess_market_status
from services.capital_fit import capital_price_policy, discovery_themes_for_capital
from services.company_discovery_service import CompanyDiscoveryService
from services.risk_policy_service import RiskPolicyService
from utils.logging import get_logger

logger = get_logger(__name__)

# Liquid names that often trade in the micro/penny range (validated live at runtime).
# Delisted / dead shells (NKLA, WISH, BBIG, etc.) must never appear here.
_MICRO_SEED_TICKERS = (
    "SOUN", "PLUG", "FCEL", "RIOT", "MARA", "OPEN", "CLOV",
    "SENS", "SIRI", "NOK", "SNAP", "F", "AAL", "SOFI",
    "NIO", "LCID", "RIVN", "AMC", "BB", "ACHR", "JOBY",
    "LUNR", "ASTS", "BITF", "CIFR", "APLD", "BBAI", "GRAB",
    "IONQ", "RXRX", "CHPT", "SPCE", "DNA", "MVST", "LAZR",
)


_PENNY_THEMES = (
    "penny stocks under $5 volume spike",
    "micro cap biotech under $5",
    "stocks under $3 breakout today",
    "cheap small cap momentum under $5",
    "low priced stocks catalyst under $5",
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
    For micro capital (e.g. $22): research affordable *live* names and build a whole-share plan.
    Honors firm risk policy (cash reserve, max position %, max gross exposure).
    """

    def __init__(
        self,
        market_provider: MarketDataProvider,
        discovery_service: CompanyDiscoveryService | None = None,
        risk_service: RiskPolicyService | None = None,
    ) -> None:
        self._market = market_provider
        self._discovery = discovery_service or CompanyDiscoveryService(market_provider)
        self._risk = risk_service or RiskPolicyService()

    def _position_count(self, capital: float) -> int:
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
        max_line = round(capital * (risk.max_position_pct / 100.0), 2)
        return cash_pct, deployable, max_line, max_gross_pct

    async def manage(
        self,
        capital: float,
        exclude_tickers: list[str] | None = None,
        max_candidates: int = 20,
    ) -> MicroPortfolioPlan:
        capital = max(1.0, float(capital))
        n_pos = self._position_count(capital)
        policy = capital_price_policy(capital, target_positions=n_pos)
        cash_pct, deployable, max_line_usd, max_gross_pct = self._risk_budgets(capital, policy.tier)
        cash_reserve = round(capital * cash_pct, 2)
        max_price = policy.max_share_price or policy.prefer_max_price
        exclude = {t.upper() for t in (exclude_tickers or [])}
        warnings: list[str] = [
            (
                f"Política de riesgo: reserva cash ≥{cash_pct * 100:.0f}%, "
                f"exposición bruta ≤{max_gross_pct * 100:.0f}%, "
                f"máx {max_line_usd:.2f}/posición ({self._risk.policy_from_settings().max_position_pct:.0f}%)."
            )
        ]

        candidates = await self._gather_candidates(
            policy=policy,
            max_price=max_price,
            exclude=exclude,
            max_candidates=max_candidates,
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

        # Rank: score first, then prefer liquid mid-penny over residual sub-$0.25 shells
        candidates.sort(key=lambda c: (-c["score"], c["price"]))
        selected = candidates[:n_pos]

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
            # Enforce per-line and book-level caps
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
            rationale = (
                f"Gestión capital micro: {shares} acciones @ ${price:.2f} = ${cost:.2f} "
                f"({pct}% del portafolio, cotización viva). {c.get('rationale', '')}"
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
                        f"Tope de posición {self._risk.policy_from_settings().max_position_pct:.0f}% "
                        "por política de riesgo; no usar el 100% del portafolio.",
                    ],
                    sources=c.get("sources") or ["capital_desk"],
                )
            )

        cash_total = round(capital - spent, 2)
        deployed_pct = round(spent / capital * 100, 1) if capital else 0.0
        tickers_txt = ", ".join(f"{l.ticker}×{l.shares}" for l in lines) or "—"
        summary = (
            f"{policy.description_es} "
            f"Plan de gestión: desplegar ${spent:.2f} ({deployed_pct}%) en {len(lines)} posiciones "
            f"({tickers_txt}); efectivo ${cash_total:.2f} "
            f"({cash_total / capital * 100:.0f}%). "
            f"Solo tickers con cotización viva; no se usa el 100% del capital."
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
        discovery_tickers = [
            c.ticker for c in report.candidates
            if c.ticker not in exclude
        ]
        discovery_meta = {c.ticker: c for c in report.candidates}

        # Validate discovery + seeds in parallel: quote + live daily bars
        seeds = [t for t in _MICRO_SEED_TICKERS if t not in exclude]
        probe_tickers = list(dict.fromkeys(discovery_tickers + seeds[:28]))
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
                # Prefer discovery over seeds; boost live volume proxy via score floor
                score += 5
                found[ticker] = {
                    "ticker": ticker,
                    "company_name": disc.company_name or probe.get("company_name"),
                    "price": price,
                    "score": score,
                    "rationale": disc.rationale,
                    "catalysts": (disc.news_headlines or [])[:3],
                    "sources": disc.sources,
                }
            elif ticker not in found:
                # Mild penalty for very thin residual pennies even if "live"
                thin_penalty = 8 if price < 0.25 else 0
                found[ticker] = {
                    "ticker": ticker,
                    "company_name": probe.get("company_name"),
                    "price": price,
                    "score": 25 + (12 if price <= 2 else 5) - thin_penalty,
                    "rationale": "Candidato líquido con cotización viva en banda micro.",
                    "catalysts": [],
                    "sources": ["seed_universe"],
                }

        return list(found.values())

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
        }
