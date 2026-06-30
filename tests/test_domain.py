"""Domain model tests."""

from domain.enums import EvidenceCategory, InvestmentRecommendation
from domain.reports import AgentReport, Finding, Reference


def test_agent_report_structure():
    report = AgentReport(
        agent_name="test_agent",
        ticker="AAPL",
        score=25.0,
        confidence=0.8,
        findings=[
            Finding(
                category=EvidenceCategory.FACT,
                statement="Revenue growth 10%",
                confidence=0.9,
                references=[Reference(source="test", data_point="revenue_growth", value=0.1)],
            )
        ],
        summary="Test summary",
    )
    assert report.ticker == "AAPL"
    assert report.score == 25.0
    assert len(report.findings) == 1


def test_investment_recommendation_values():
    assert InvestmentRecommendation.STRONG_BUY.value == "strong_buy"
    assert InvestmentRecommendation.HOLD.value == "hold"
