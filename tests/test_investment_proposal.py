"""Investment proposal service tests."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from domain.enums import InvestmentRecommendation
from domain.proposal import InstrumentType, RiskProfile
from domain.reports import InvestmentThesis, ScenarioCase
from services.investment_proposal_service import InvestmentProposalService


def _thesis(ticker: str, rec: InvestmentRecommendation, conf: float = 0.8) -> InvestmentThesis:
    case = ScenarioCase(name="Base", probability=0.5, thesis="test", confidence=0.7)
    return InvestmentThesis(
        ticker=ticker,
        executive_summary="test",
        investment_thesis="test",
        bull_case=case,
        bear_case=case,
        base_case=case,
        recommendation=rec,
        confidence=conf,
    )


@pytest.fixture
def mock_market():
    market = MagicMock()
    market.get_quote = AsyncMock(
        side_effect=lambda t: {
            "ticker": t,
            "company_name": t,
            "current_price": 100.0 if t == "EXPENSIVE" else 25.0,
        }
    )
    # Synthetic daily returns for optimizer
    hist = pd.DataFrame({"Close": [100 + i * 0.5 for i in range(60)]})
    market.get_history = AsyncMock(return_value=hist)
    return market


@pytest.mark.asyncio
async def test_proposal_cfd_for_small_budget(mock_market):
    svc = InvestmentProposalService(mock_market)
    proposal = await svc.build_proposal(
        budget=50,
        theses=[_thesis("EXPENSIVE", InvestmentRecommendation.BUY)],
        instrument_mode=InstrumentType.AUTO,
        risk_profile=RiskProfile.BALANCED,
    )
    assert proposal.budget == 50
    assert len(proposal.allocations) >= 1
    line = proposal.allocations[0]
    assert line.instrument == InstrumentType.CFD
    assert line.margin_required is not None
    assert line.margin_required > 0


@pytest.mark.asyncio
async def test_proposal_stock_when_affordable(mock_market):
    svc = InvestmentProposalService(mock_market)
    proposal = await svc.build_proposal(
        budget=500,
        theses=[_thesis("CHEAP", InvestmentRecommendation.STRONG_BUY)],
        instrument_mode=InstrumentType.STOCK,
        risk_profile=RiskProfile.BALANCED,
    )
    assert proposal.allocations[0].instrument == InstrumentType.STOCK
    assert proposal.allocations[0].margin_required is None


@pytest.mark.asyncio
async def test_proposal_prefers_affordable_over_expensive_on_micro_budget(mock_market):
    """With $50 capital, prefer a $3 stock over a $100 name when both are buys."""
    mock_market.get_quote = AsyncMock(
        side_effect=lambda t: {
            "ticker": t,
            "company_name": t,
            "current_price": 3.0 if t == "PENNY" else 100.0,
        }
    )
    svc = InvestmentProposalService(mock_market)
    proposal = await svc.build_proposal(
        budget=50,
        theses=[
            _thesis("EXPENSIVE", InvestmentRecommendation.BUY, 0.9),
            _thesis("PENNY", InvestmentRecommendation.BUY, 0.75),
        ],
        instrument_mode=InstrumentType.AUTO,
        prefer_affordable=True,
    )
    tickers = [a.ticker for a in proposal.allocations]
    assert "PENNY" in tickers
    # Expensive should be filtered out of micro band when cheaper options exist
    assert "EXPENSIVE" not in tickers or all(a.price <= 5 for a in proposal.allocations if a.ticker == "PENNY")
    penny_line = next(a for a in proposal.allocations if a.ticker == "PENNY")
    assert penny_line.instrument == InstrumentType.STOCK
    assert any("micro" in w.lower() or "penny" in w.lower() for w in proposal.warnings)