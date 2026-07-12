"""Tests for discover → proposal flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.discovery import DiscoveryCandidate, DiscoveryReport
from domain.enums import InvestmentRecommendation
from domain.proposal import AllocationLine, InstrumentType, InvestmentProposal, RiskProfile
from domain.reports import InvestmentThesis, ScenarioCase
from services.discovery_proposal_service import DiscoveryProposalService


def _mock_thesis(ticker: str) -> InvestmentThesis:
    case = ScenarioCase(name="Base", probability=0.5, thesis="test", confidence=0.7)
    return InvestmentThesis(
        ticker=ticker,
        executive_summary=f"{ticker} momentum.",
        investment_thesis="test",
        bull_case=case,
        bear_case=case,
        base_case=case,
        recommendation=InvestmentRecommendation.BUY,
        confidence=0.75,
    )


def _mock_proposal(tickers: list[str], budget: float) -> InvestmentProposal:
    allocs = [
        AllocationLine(
            ticker=t,
            recommendation="buy",
            confidence=0.75,
            allocation_usd=budget / len(tickers),
            allocation_pct=100 / len(tickers),
            instrument=InstrumentType.STOCK,
            price=100.0,
            notional_exposure=budget / len(tickers),
            units=(budget / len(tickers)) / 100,
            purchase_order=i + 1,
            rationale=f"Descubierto {t}",
        )
        for i, t in enumerate(tickers)
    ]
    return InvestmentProposal(
        budget=budget,
        risk_profile=RiskProfile.BALANCED,
        instrument_mode=InstrumentType.AUTO,
        default_cfd_margin_pct=20.0,
        cash_reserve_pct=5.0,
        allocations=allocs,
        summary=f"Propuesta con {', '.join(tickers)}",
    )


@pytest.mark.asyncio
async def test_discover_and_propose_success():
    report = DiscoveryReport(
        candidates=[
            DiscoveryCandidate(ticker="NVDA", score=90, mention_count=5, sources=["news"]),
            DiscoveryCandidate(ticker="AMD", score=80, mention_count=3, sources=["reddit"]),
        ],
        summary="2 candidatos",
    )

    discovery = AsyncMock()
    discovery.research = AsyncMock(return_value=report)

    analysis = AsyncMock()
    analysis.analyze_ticker = AsyncMock(side_effect=lambda t, **kw: _mock_thesis(t))

    proposal = AsyncMock()
    proposal.build_proposal = AsyncMock(return_value=_mock_proposal(["NVDA", "AMD"], 1000))

    watchlist = AsyncMock()
    watchlist.add = AsyncMock(return_value=None)

    svc = DiscoveryProposalService(discovery, analysis, proposal, watchlist)
    result = await svc.discover_and_propose(budget=1000, proposal_top=2)

    assert result.tickers_selected == ["NVDA", "AMD"]
    assert len(result.proposal.allocations) == 2
    assert result.watchlist_added == ["NVDA", "AMD"]
    assert "Descubiertos" in result.summary
    discovery.research.assert_called_once()
    assert analysis.analyze_ticker.call_count == 2
    proposal.build_proposal.assert_called_once()


@pytest.mark.asyncio
async def test_discover_and_propose_no_candidates():
    discovery = AsyncMock()
    discovery.research = AsyncMock(return_value=DiscoveryReport(candidates=[]))

    svc = DiscoveryProposalService(discovery, AsyncMock(), AsyncMock())
    with pytest.raises(ValueError, match="No se encontraron candidatos"):
        await svc.discover_and_propose(budget=500)


@pytest.mark.asyncio
async def test_discover_and_propose_analysis_failure():
    report = DiscoveryReport(
        candidates=[DiscoveryCandidate(ticker="XYZ", score=50, mention_count=1)],
    )
    discovery = AsyncMock()
    discovery.research = AsyncMock(return_value=report)

    analysis = AsyncMock()
    analysis.analyze_ticker = AsyncMock(side_effect=RuntimeError("fail"))

    svc = DiscoveryProposalService(discovery, analysis, AsyncMock())
    with pytest.raises(ValueError, match="No se pudo analizar"):
        await svc.discover_and_propose(budget=500)
