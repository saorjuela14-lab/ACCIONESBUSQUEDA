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
async def test_proposal_skips_sell_recommendations(mock_market):
    svc = InvestmentProposalService(mock_market)
    proposal = await svc.build_proposal(
        budget=50,
        theses=[_thesis("BAD", InvestmentRecommendation.SELL)],
        instrument_mode=InstrumentType.AUTO,
    )
    assert len(proposal.allocations) == 0
