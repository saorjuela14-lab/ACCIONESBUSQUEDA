"""Tests for unanimous dual-horizon committee BUY gate."""

from domain.enums import InvestmentRecommendation, StrategyType, TimeHorizon
from domain.reports import AgentReport, InvestmentThesis, ScenarioCase, StrategyConclusion
from services.committee_consensus import (
    SOURCE_TAG,
    VOTING_AGENTS,
    evaluate_consensus,
    score_to_recommendation,
)


def _report(name: str, score: float) -> AgentReport:
    return AgentReport(
        agent_name=name,
        ticker="TEST",
        score=score,
        confidence=0.7,
        summary=f"{name} ok",
    )


def _strategy(st: StrategyType, score: float, horizon: TimeHorizon) -> StrategyConclusion:
    return StrategyConclusion(
        strategy=st,
        score=score,
        confidence=0.7,
        conclusion="ok",
        horizon=horizon,
    )


def _full_thesis(
    *,
    agent_score: float = 20.0,
    short_score: float = 20.0,
    long_score: float = 20.0,
    dividend_score: float = 0.0,
    recommendation: InvestmentRecommendation = InvestmentRecommendation.BUY,
) -> InvestmentThesis:
    case = ScenarioCase(name="Base", probability=1.0, thesis="x", confidence=0.5)
    reports = [_report(name, agent_score) for name in sorted(VOTING_AGENTS)]
    strategies = [
        _strategy(StrategyType.MOMENTUM, short_score, TimeHorizon.WEEKLY),
        _strategy(StrategyType.SWING, short_score, TimeHorizon.WEEKLY),
        _strategy(StrategyType.BREAKOUT, short_score, TimeHorizon.INTRADAY),
        _strategy(StrategyType.VALUE, long_score, TimeHorizon.LONG_TERM),
        _strategy(StrategyType.GROWTH, long_score, TimeHorizon.LONG_TERM),
        _strategy(StrategyType.DIVIDEND, dividend_score, TimeHorizon.LONG_TERM),
    ]
    return InvestmentThesis(
        ticker="TEST",
        executive_summary="test",
        investment_thesis="test",
        bull_case=case,
        bear_case=case,
        base_case=case,
        recommendation=recommendation,
        confidence=0.7,
        agent_reports=reports,
        strategy_conclusions=strategies,
    )


def test_score_to_recommendation_thresholds():
    assert score_to_recommendation(40) == InvestmentRecommendation.STRONG_BUY
    assert score_to_recommendation(15) == InvestmentRecommendation.BUY
    assert score_to_recommendation(0) == InvestmentRecommendation.HOLD
    assert score_to_recommendation(-20) == InvestmentRecommendation.SELL


def test_unanimous_dual_horizon_passes():
    verdict = evaluate_consensus(_full_thesis())
    assert verdict.passed is True
    assert verdict.agents_unanimous_buy is True
    assert verdict.short_horizon_buy is True
    assert verdict.long_horizon_buy is True
    assert verdict.source_tag == SOURCE_TAG


def test_one_agent_hold_fails():
    thesis = _full_thesis()
    reports = [
        r.model_copy(update={"score": 0.0}) if r.agent_name == "technical_agent" else r
        for r in thesis.agent_reports
    ]
    thesis = thesis.model_copy(update={"agent_reports": reports})
    verdict = evaluate_consensus(thesis)
    assert verdict.passed is False
    assert any("technical_agent" in x for x in verdict.reasons)


def test_short_horizon_weak_fails():
    verdict = evaluate_consensus(_full_thesis(short_score=5.0))
    assert verdict.passed is False
    assert verdict.short_horizon_buy is False


def test_long_horizon_weak_fails():
    verdict = evaluate_consensus(_full_thesis(long_score=5.0))
    assert verdict.passed is False
    assert verdict.long_horizon_buy is False


def test_dividend_sell_blocks_long():
    verdict = evaluate_consensus(_full_thesis(dividend_score=-20.0))
    assert verdict.passed is False


def test_micro_soft_allows_one_agent_hold():
    from services.committee_consensus import SOURCE_TAG_SOFT

    thesis = _full_thesis()
    reports = [
        r.model_copy(update={"score": 0.0}) if r.agent_name == "technical_agent" else r
        for r in thesis.agent_reports
    ]
    thesis = thesis.model_copy(
        update={
            "agent_reports": reports,
            "recommendation": InvestmentRecommendation.HOLD,
        }
    )
    # Strict fails
    assert evaluate_consensus(thesis).passed is False
    # Micro majority (9/10 BUY, thesis HOLD OK, horizons still BUY) passes
    soft = evaluate_consensus(thesis, mode="micro")
    assert soft.passed is True
    assert soft.source_tag == SOURCE_TAG_SOFT


def test_micro_soft_allows_non_bearish_long_without_value_buy():
    """Pennies often lack VALUE BUY — non-bearish long avg should still pass micro."""
    thesis = _full_thesis(long_score=0.0)  # HOLD on value+growth
    soft = evaluate_consensus(thesis, mode="micro")
    assert soft.passed is True
    assert soft.long_horizon_buy is True


def test_micro_soft_blocks_strong_sell_cluster():
    thesis = _full_thesis(agent_score=-40.0, short_score=-40.0, long_score=-40.0)
    thesis = thesis.model_copy(update={"recommendation": InvestmentRecommendation.STRONG_SELL})
    assert evaluate_consensus(thesis, mode="micro").passed is False
