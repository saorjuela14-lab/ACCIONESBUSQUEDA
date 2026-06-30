"""Investment proposal v2 — optimization, CFD costs, executive report."""

from __future__ import annotations

import asyncio

import numpy as np

from domain.enums import InvestmentRecommendation
from domain.proposal import (
    AllocationLine,
    ExecutiveInvestmentReport,
    InstrumentType,
    InvestmentProposal,
    RiskProfile,
)
from domain.reports import InvestmentThesis
from providers.interfaces import MarketDataProvider
from services.correlation_service import CorrelationService
from services.knowledge_graph_service import KnowledgeGraphService
from services.portfolio_optimizer_service import PortfolioOptimizerService
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_LINE_USD = 5.0
CFD_SPREAD_BPS = 8.0
CFD_OVERNIGHT_ANNUAL_PCT = 6.5
CFD_MARGIN_DEFAULTS = {RiskProfile.CONSERVATIVE: 25.0, RiskProfile.BALANCED: 20.0, RiskProfile.AGGRESSIVE: 10.0}
CASH_RESERVE = {RiskProfile.CONSERVATIVE: 0.15, RiskProfile.BALANCED: 0.10, RiskProfile.AGGRESSIVE: 0.05}
REC_WEIGHT = {
    InvestmentRecommendation.STRONG_BUY: 1.0,
    InvestmentRecommendation.BUY: 0.75,
    InvestmentRecommendation.HOLD: 0.35,
    InvestmentRecommendation.SELL: 0.0,
    InvestmentRecommendation.STRONG_SELL: 0.0,
}


class InvestmentProposalService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider
        self._optimizer = PortfolioOptimizerService()
        self._correlation = CorrelationService(market_provider)
        self._graph = KnowledgeGraphService()

    def _cfd_costs(self, notional: float) -> tuple[float, float]:
        spread = notional * (CFD_SPREAD_BPS / 10000)
        overnight_daily = notional * (CFD_OVERNIGHT_ANNUAL_PCT / 100 / 365)
        return round(spread, 2), round(overnight_daily, 4)

    def _choose_instrument(
        self, price: float, line_budget: float, mode: InstrumentType, margin_pct: float
    ) -> tuple[InstrumentType, float, float, float | None]:
        if mode == InstrumentType.STOCK:
            shares = int(line_budget // price) if price > 0 else 0
            if shares < 1:
                m = line_budget * margin_pct / 100
                return InstrumentType.CFD, line_budget / price if price else 0, line_budget, m
            return InstrumentType.STOCK, float(shares), shares * price, None
        if mode == InstrumentType.CFD:
            m = line_budget * margin_pct / 100
            return InstrumentType.CFD, line_budget / price if price else 0, line_budget, m
        if price > 0 and line_budget >= price:
            shares = int(line_budget // price)
            return InstrumentType.STOCK, float(shares), shares * price, None
        m = line_budget * margin_pct / 100
        return InstrumentType.CFD, line_budget / price if price else 0, line_budget, m

    async def _build_returns_matrix(self, tickers: list[str]) -> np.ndarray:
        rows = []
        for t in tickers:
            df = await self._market.get_history(t, period="6mo", interval="1d")
            if df.empty or "Close" not in df.columns:
                continue
            rows.append(df["Close"].pct_change().dropna().values[-60:])
        if not rows:
            return np.zeros((1, len(tickers)))
        min_len = min(len(r) for r in rows)
        return np.array([r[-min_len:] for r in rows])

    def _executive_report(
        self,
        budget: float,
        allocations: list[AllocationLine],
        excluded: list[tuple[str, str]],
        theses: dict[str, InvestmentThesis],
    ) -> ExecutiveInvestmentReport:
        why_sel = [
            f"{a.ticker}: {a.recommendation.upper()} @ {a.confidence:.0%} — {a.rationale[:120]}"
            for a in allocations
        ]
        why_not = [f"{t}: {reason}" for t, reason in excluded]
        risks, events, corr_notes, invalidations, cfd_notes = [], [], [], [], []

        for a in allocations:
            thesis = theses.get(a.ticker)
            if thesis:
                for r in thesis.risks[:2]:
                    risks.append(f"{a.ticker}: {r.statement[:100]}")
                invalidations.append(
                    f"{a.ticker}: bear case if {thesis.bear_case.thesis[:80]}"
                )
            try:
                graph = self._graph.subgraph_for_ticker(a.ticker, depth=1)
                if graph.at_risk:
                    events.append(f"{a.ticker}: monitor {', '.join(graph.at_risk[:2])}")
            except Exception:
                pass
            if a.instrument == InstrumentType.CFD:
                cfd_notes.append(
                    f"{a.ticker}: CFD chosen — fractional exposure ${a.notional_exposure:.2f}, "
                    f"margin ${a.margin_required:.2f} ({a.margin_pct}%), "
                    f"spread est ${a.spread_cost_est}, overnight est ${a.overnight_financing_est}/day"
                )

        exp_ret = sum((a.expected_return_pct or 0) * a.allocation_pct / 100 for a in allocations)
        max_loss = sum((a.max_loss_est or a.notional_exposure * 0.15) for a in allocations) / budget * 100 if budget else 0

        narrative = (
            f"Professional allocation of ${budget:.2f} across {len(allocations)} positions. "
            f"Expected portfolio return ~{exp_ret:.1f}%, estimated max loss ~{max_loss:.1f}%. "
            f"{len([a for a in allocations if a.instrument == InstrumentType.CFD])} CFD and "
            f"{len([a for a in allocations if a.instrument == InstrumentType.STOCK])} stock positions."
        )

        return ExecutiveInvestmentReport(
            why_selected=why_sel,
            why_excluded=why_not,
            key_risks=risks[:8],
            events_to_monitor=events[:6],
            correlation_notes=corr_notes,
            invalidation_scenarios=invalidations[:6],
            expected_return_pct=round(exp_ret, 2),
            max_loss_est_pct=round(max_loss, 2),
            portfolio_risk_score=round(max_loss * 1.2, 1),
            cfd_rationale=cfd_notes,
            narrative=narrative,
        )

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
            warnings.append("Budget under $20 — limited diversification.")

        candidates: list[tuple[str, InvestmentThesis, float]] = []
        excluded: list[tuple[str, str]] = []
        thesis_map: dict[str, InvestmentThesis] = {}

        for thesis in theses:
            t = thesis.ticker.upper()
            thesis_map[t] = thesis
            if tickers_filter and t not in {x.upper() for x in tickers_filter}:
                continue
            w = REC_WEIGHT.get(thesis.recommendation, 0) * thesis.confidence
            if w <= 0:
                excluded.append((t, f"recommendation {thesis.recommendation.value}"))
                continue
            candidates.append((t, thesis, w))

        if not candidates:
            return InvestmentProposal(
                budget=budget,
                risk_profile=risk_profile,
                instrument_mode=instrument_mode,
                default_cfd_margin_pct=margin_pct,
                cash_reserve_pct=reserve_pct * 100,
                unallocated_cash=budget,
                warnings=warnings + ["No buy-rated tickers."],
                summary="No proposal generated.",
            )

        tickers = [c[0] for c in candidates]
        scores = [c[2] * 100 for c in candidates]
        confs = [c[1].confidence for c in candidates]

        returns_matrix = await self._build_returns_matrix(tickers)
        cov = self._optimizer.build_covariance(returns_matrix)
        exp_returns = self._optimizer.estimate_returns(scores, confs)
        opt_weights = self._optimizer.optimize(tickers, exp_returns, cov, risk_profile)

        correlation_notes: list[str] = []
        if len(tickers) >= 2:
            try:
                corr_view = await self._correlation.analyze(tickers[0])
                for dep in (corr_view.company_dependencies or [])[:3]:
                    correlation_notes.append(f"{dep.ticker}: {dep.relationship} — {dep.why_it_matters[:80]}")
            except Exception:
                pass

        allocations: list[AllocationLine] = []
        total_margin = total_spread = total_overnight = 0.0
        order = 1

        for ticker, thesis, _ in sorted(candidates, key=lambda x: opt_weights.get(x[0], 0), reverse=True):
            weight = opt_weights.get(ticker, 0)
            line_budget = deployable * weight
            if line_budget < MIN_LINE_USD:
                excluded.append((ticker, "weight below minimum after optimization"))
                continue

            quote = await self._market.get_quote(ticker)
            price = float(quote.get("current_price") or 0)
            if price <= 0:
                excluded.append((ticker, "price unavailable"))
                continue

            instrument, units, notional, margin = self._choose_instrument(
                price, line_budget, instrument_mode, margin_pct
            )
            spread_cost, overnight = (None, None)
            if instrument == InstrumentType.CFD:
                spread_cost, overnight = self._cfd_costs(notional)
                total_margin += margin or 0
                total_spread += spread_cost or 0
                total_overnight += overnight or 0
                rationale = (
                    f"CFD: ${notional:.2f} exposure ({units:.4f} units @ ${price:.2f}), "
                    f"margin ${margin:.2f}, spread ~${spread_cost}, overnight ~${overnight}/day"
                )
            else:
                rationale = f"Stock: {int(units)} shares @ ${price:.2f} = ${notional:.2f}"

            exp_ret = thesis.confidence * REC_WEIGHT.get(thesis.recommendation, 0) * 12
            stop = round(price * 0.92, 2) if instrument == InstrumentType.STOCK else round(price * 0.95, 2)
            max_loss = notional * (0.08 if instrument == InstrumentType.STOCK else 0.15)

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
                    spread_cost_est=spread_cost,
                    overnight_financing_est=overnight,
                    stop_loss_suggested=stop,
                    max_loss_est=round(max_loss, 2),
                    expected_return_pct=round(exp_ret, 2),
                    horizon="medium_term",
                    purchase_order=order,
                    rationale=rationale,
                )
            )
            order += 1

        allocated = sum(a.allocation_usd for a in allocations)
        exec_report = self._executive_report(budget, allocations, excluded, thesis_map)
        if correlation_notes:
            exec_report.correlation_notes = correlation_notes

        if total_margin:
            warnings.append(f"Total CFD margin required: ${total_margin:.2f}. Leverage amplifies risk.")

        return InvestmentProposal(
            budget=budget,
            risk_profile=risk_profile,
            instrument_mode=instrument_mode,
            default_cfd_margin_pct=margin_pct,
            cash_reserve_pct=reserve_pct * 100,
            allocations=allocations,
            unallocated_cash=max(0, round(budget - allocated - budget * reserve_pct, 2)),
            total_margin_required=round(total_margin, 2) if total_margin else None,
            total_spread_cost=round(total_spread, 2) if total_spread else None,
            total_overnight_est=round(total_overnight, 4) if total_overnight else None,
            portfolio_expected_return_pct=exec_report.expected_return_pct,
            portfolio_max_loss_pct=exec_report.max_loss_est_pct,
            diversification_score=round(min(100, len(allocations) * 25), 1),
            instrument_summary=(
                f"{sum(1 for a in allocations if a.instrument == InstrumentType.STOCK)} stocks, "
                f"{sum(1 for a in allocations if a.instrument == InstrumentType.CFD)} CFDs. "
                f"Optimized weights via mean-variance."
            ),
            warnings=warnings,
            summary=exec_report.narrative,
            executive_report=exec_report,
        )
