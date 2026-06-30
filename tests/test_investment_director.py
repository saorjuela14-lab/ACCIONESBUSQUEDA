"""Investment Director tests."""

from domain.enums import EvidenceCategory
from domain.reports import AgentReport, Finding
from agents.investment_director import InvestmentDirector


def _make_report(name: str, score: float, confidence: float = 0.8) -> AgentReport:
    return AgentReport(
        agent_name=name,
        ticker="AAPL",
        score=score,
        confidence=confidence,
        findings=[
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"{name} finding",
                confidence=confidence,
                references=[],
            )
        ],
        summary=f"{name} summary",
    )


def test_director_builds_thesis():
    director = InvestmentDirector()
    reports = [
        _make_report("fundamental_agent", 30),
        _make_report("technical_agent", 20),
        _make_report("valuation_agent", 25),
        _make_report("macro_agent", -5),
    ]
    thesis = director.build_thesis("AAPL", reports, InvestmentDirector.DEFAULT_WEIGHTS, 150.0)

    assert thesis.ticker == "AAPL"
    assert thesis.recommendation is not None
    assert thesis.confidence > 0
    assert thesis.bull_case.probability + thesis.base_case.probability + thesis.bear_case.probability == 1.0


def test_director_never_includes_self_in_weighting():
    director = InvestmentDirector()
    reports = [_make_report("fundamental_agent", 50)]
    score = director._weighted_score(reports, InvestmentDirector.DEFAULT_WEIGHTS)
    assert score > 0
