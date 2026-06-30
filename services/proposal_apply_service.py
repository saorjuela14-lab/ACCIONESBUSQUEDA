"""Apply investment proposal allocations to a portfolio."""

from __future__ import annotations

from domain.proposal import InstrumentType, InvestmentProposal
from domain.entities import Portfolio
from services.portfolio_service import PortfolioService
from utils.logging import get_logger

logger = get_logger(__name__)


class ProposalApplyService:
    def __init__(self, portfolio_service: PortfolioService) -> None:
        self._portfolio = portfolio_service

    async def apply(self, portfolio_id: str, proposal: InvestmentProposal) -> tuple[Portfolio, list[str]]:
        warnings: list[str] = []
        portfolio = await self._portfolio.refresh_prices(portfolio_id)

        for line in sorted(proposal.allocations, key=lambda x: x.purchase_order):
            cost = line.allocation_usd
            if line.instrument == InstrumentType.CFD:
                cost = line.margin_required or line.allocation_usd
                warnings.append(
                    f"{line.ticker}: CFD — registrado como posición fraccional ({line.units:.4f} units)"
                )

            if portfolio.cash < cost:
                warnings.append(f"{line.ticker}: cash insuficiente (${portfolio.cash:.2f} < ${cost:.2f})")
                continue

            shares = line.units if line.units > 0 else line.allocation_usd / max(line.price, 0.01)
            portfolio = await self._portfolio.add_position(
                portfolio_id, line.ticker, shares, line.price
            )

        logger.info("proposal.applied", portfolio_id=portfolio_id, positions=len(proposal.allocations))
        return portfolio, warnings
