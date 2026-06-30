"""Strategy Lab tests."""

from domain.reports import AgentReport, Finding
from domain.enums import EvidenceCategory, StrategyType
from services.strategy_lab import StrategyLab


def test_strategy_lab_generates_all_strategies():
    lab = StrategyLab()
    reports = [
        AgentReport(
            agent_name="fundamental_agent",
            ticker="AAPL",
            score=20,
            confidence=0.8,
            findings=[Finding(category=EvidenceCategory.FACT, statement="test", confidence=0.8, references=[])],
            summary="fundamental",
        ),
        AgentReport(
            agent_name="technical_agent",
            ticker="AAPL",
            score=15,
            confidence=0.7,
            findings=[Finding(category=EvidenceCategory.FACT, statement="test", confidence=0.7, references=[])],
            summary="technical",
        ),
    ]
    conclusions = lab.evaluate(reports)
    strategies = {c.strategy for c in conclusions}
    assert StrategyType.VALUE in strategies
    assert StrategyType.MOMENTUM in strategies
    assert len(conclusions) >= 9
