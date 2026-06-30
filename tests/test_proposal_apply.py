"""Proposal apply service tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.entities import Portfolio, PortfolioPosition
from domain.enums import StrategyType
from domain.proposal import AllocationLine, InstrumentType, InvestmentProposal, RiskProfile
from services.proposal_apply_service import ProposalApplyService


@pytest.fixture
def portfolio():
    return Portfolio(
        id="p1",
        name="Test",
        strategy=StrategyType.GROWTH,
        initial_capital=1000,
        cash=1000,
        positions=[],
    )


@pytest.fixture
def proposal():
    return InvestmentProposal(
        budget=50,
        risk_profile=RiskProfile.BALANCED,
        instrument_mode=InstrumentType.AUTO,
        default_cfd_margin_pct=20,
        cash_reserve_pct=10,
        allocations=[
            AllocationLine(
                ticker="RNA",
                recommendation="buy",
                confidence=0.8,
                allocation_usd=25,
                allocation_pct=50,
                instrument=InstrumentType.STOCK,
                price=13.0,
                notional_exposure=25,
                units=1.92,
                rationale="test",
            )
        ],
    )


@pytest.mark.asyncio
async def test_apply_proposal_adds_position(portfolio, proposal):
    svc_mock = MagicMock()
    svc_mock.refresh_prices = AsyncMock(return_value=portfolio)
    svc_mock.add_position = AsyncMock(
        return_value=Portfolio(
            id="p1",
            name="Test",
            strategy=StrategyType.GROWTH,
            initial_capital=1000,
            cash=975,
            positions=[PortfolioPosition(ticker="RNA", shares=1.92, average_cost=13.0)],
        )
    )
    apply_svc = ProposalApplyService(svc_mock)
    result, warnings = await apply_svc.apply("p1", proposal)
    assert result.positions[0].ticker == "RNA"
    svc_mock.add_position.assert_called_once()
