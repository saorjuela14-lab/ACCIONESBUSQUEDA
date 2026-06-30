"""Investment proposal builder — budget allocation, stocks vs CFDs, margin."""

from __future__ import annotations

from domain.enums import InvestmentRecommendation
from domain.proposal import AllocationLine, InstrumentType, InvestmentProposal, RiskProfile
from domain.reports import InvestmentThesis
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)

# Minimum notional for a line (avoid dust positions)
MIN_LINE_USD = 5.0

CFD_MARGIN_DEFAULTS: dict[RiskProfile, float] = {
    RiskProfile.CONSERVATIVE: 25.0,
    RiskProfile.BALANCED: 20.0,
    RiskProfile.AGGRESSIVE: 10.0,
}

CASH_RESERVE: dict[RiskProfile, float] = {
    RiskProfile.CONSERVATIVE: 0.15,
    RiskProfile.BALANCED: 0.10,
    RiskProfile.AGGRESSIVE: 0.05,
}

RECOMMENDATION_WEIGHT: dict[InvestmentRecommendation, float] = {
    InvestmentRecommendation.STRONG_BUY: 1.0,
    InvestmentRecommendation.BUY: 0.75,
    InvestmentRecommendation.HOLD: 0.35,
    InvestmentRecommendation.SELL: 0.0,
    InvestmentRecommendation.STRONG_SELL: 0.0,
}


class InvestmentProposalService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    def _choose_instrument(
        self,
        price: float,
        line_budget: float,
        mode: InstrumentType,
        margin_pct: float,
    ) -> tuple[InstrumentType, float, float, float | None]:
        """Return instrument, units, notional, margin_required."""
        if mode == InstrumentType.STOCK:
            shares = int(line_budget // price) if price > 0 else 0
            if shares < 1:
                return InstrumentType.CFD, line_budget / price if price else 0, line_budget, line_budget * margin_pct / 100
            notional = shares * price
            return InstrumentType.STOCK, float(shares), notional, None

        if mode == InstrumentType.CFD:
            units = line_budget / price if price > 0 else 0
            notional = line_budget
            margin = notional * margin_pct / 100
            return InstrumentType.CFD, units, notional, margin

        # AUTO: stock if budget buys >=1 whole share, else CFD for fractional
        if price > 0 and line_budget >= price:
            shares = int(line_budget // price)
            notional = shares * price
            leftover = line_budget - notional
            if leftover >= MIN_LINE_USD and leftover < price:
                # Split: whole shares + CFD for remainder
                cfd_notional = leftover
                return InstrumentType.STOCK, float(shares), notional, None
            return InstrumentType.STOCK, float(shares), notional, None

        units = line_budget / price if price > 0 else 0
        return InstrumentType.CFD, units, line_budget, line_budget * margin_pct / 100

    async def build_proposal(
        self,
        budget: float,
        theses: list[InvestmentThesis],
        instrument_mode: InstrumentType = InstrumentType.AUTO,
        risk_profile: RiskProfile = RiskProfile.BALANCED,
        cfd_margin_pct: float | None = None,
        tickers_filter: list[str] | None = None,
    ) -> InvestmentProposal:
        margin_pct = cfd_margin_pct or CFD_MARGIN_DEFAULTS[risk_profile]
        reserve_pct = CASH_RESERVE[risk_profile]
        deployable = budget * (1 - reserve_pct)

        warnings: list[str] = []
        if budget < 20:
            warnings.append("Budget under $20 — very limited diversification; consider accumulating before investing.")

        candidates: list[tuple[str, InvestmentThesis, float]] = []
        for thesis in theses:
            t = thesis.ticker.upper()
            if tickers_filter and t not in {x.upper() for x in tickers_filter}:
                continue
            weight = RECOMMENDATION_WEIGHT.get(thesis.recommendation, 0.0) * thesis.confidence
            if weight <= 0:
                continue
            candidates.append((t, thesis, weight))

        if not candidates:
            return InvestmentProposal(
                budget=budget,
                risk_profile=risk_profile,
                instrument_mode=instrument_mode,
                default_cfd_margin_pct=margin_pct,
                cash_reserve_pct=reserve_pct * 100,
                unallocated_cash=budget,
                warnings=warnings + ["No buy-rated tickers in input — no allocation proposed."],
                summary="No investment proposal generated: no qualifying buy candidates.",
            )

        total_weight = sum(c[2] for c in candidates)
        allocations: list[AllocationLine] = []
        total_margin = 0.0

        for ticker, thesis, weight in candidates:
            line_budget = deployable * (weight / total_weight)
            if line_budget < MIN_LINE_USD:
                continue

            quote = await self._market.get_quote(ticker)
            price = float(quote.get("current_price") or 0)
            if price <= 0:
                warnings.append(f"{ticker}: price unavailable — skipped")
                continue

            instrument, units, notional, margin = self._choose_instrument(
                price, line_budget, instrument_mode, margin_pct
            )

            if instrument == InstrumentType.CFD:
                total_margin += margin or 0.0
                rationale = (
                    f"CFD allows ${line_budget:.2f} exposure on a ${price:.2f} stock "
                    f"({units:.4f} units) with ~{margin_pct:.0f}% margin (${(margin or 0):.2f} required)."
                )
            else:
                rationale = (
                    f"Whole shares: {int(units)} x ${price:.2f} = ${notional:.2f}. "
                    f"Full ownership, no margin, eligible for dividends."
                )

            allocations.append(
                AllocationLine(
                    ticker=ticker,
                    company_name=quote.get("company_name"),
                    recommendation=thesis.recommendation.value,
                    confidence=thesis.confidence,
                    allocation_usd=round(notional, 2),
                    allocation_pct=round(notional / budget * 100, 1),
                    instrument=instrument,
                    price=price,
                    notional_exposure=round(notional, 2),
                    units=round(units, 4),
                    margin_required=round(margin, 2) if margin else None,
                    margin_pct=margin_pct if instrument == InstrumentType.CFD else None,
                    rationale=rationale,
                )
            )

        allocated = sum(a.allocation_usd for a in allocations)
        unallocated = round(budget - allocated - budget * reserve_pct, 2)

        stock_count = sum(1 for a in allocations if a.instrument == InstrumentType.STOCK)
        cfd_count = sum(1 for a in allocations if a.instrument == InstrumentType.CFD)
        instrument_summary = (
            f"{stock_count} stock position(s), {cfd_count} CFD position(s). "
            f"Cash reserve {reserve_pct*100:.0f}% (${budget * reserve_pct:.2f})."
        )

        if cfd_count > 0:
            warnings.append(
                f"CFD positions require margin (~${total_margin:.2f} total). "
                "Leverage amplifies gains AND losses. Not suitable for all investors."
            )

        summary = (
            f"Proposal for ${budget:.2f}: deploy ${allocated:.2f} across {len(allocations)} position(s). "
            f"{instrument_summary} "
            f"Based on committee recommendations weighted by confidence."
        )

        return InvestmentProposal(
            budget=budget,
            risk_profile=risk_profile,
            instrument_mode=instrument_mode,
            default_cfd_margin_pct=margin_pct,
            cash_reserve_pct=reserve_pct * 100,
            allocations=allocations,
            unallocated_cash=max(0, unallocated),
            total_margin_required=round(total_margin, 2) if total_margin else None,
            instrument_summary=instrument_summary,
            warnings=warnings,
            summary=summary,
        )
