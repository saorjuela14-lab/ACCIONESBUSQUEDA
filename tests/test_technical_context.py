"""Technical context correlation tests."""

from domain.reports import AgentReport

from agents.technical.context import (
    build_prior_context,
    correlate_technical_with_context,
)


def _report(name: str, score: float, summary: str = "", raw_data: dict | None = None) -> AgentReport:
    return AgentReport(
        agent_name=name,
        ticker="TEST",
        score=score,
        confidence=0.8,
        summary=summary or name,
        raw_data=raw_data or {},
    )


def test_build_prior_context_extracts_scores():
    reports = [
        _report("fundamental_agent", 25),
        _report("news_agent", -20, raw_data={"sentiment_score": -15}),
        _report(
            "market_dependency_agent",
            5,
            raw_data={
                "benchmark_correlations": [{"ticker": "SPY", "correlation": 0.72, "interpretation": "high beta"}],
                "macro_sensitivities": [{"factor": "rates", "sensitivity": "high", "scenario": "hike"}],
                "company_dependencies": [{"ticker": "NVDA", "relationship": "supplier", "correlation": 0.55, "why_it_matters": "AI demand"}],
            },
        ),
    ]
    ctx = build_prior_context(reports)
    assert ctx.scores["fundamental_agent"] == 25
    assert ctx.benchmark_correlations[0]["ticker"] == "SPY"
    assert ctx.fundamental_score == 25


def test_bullish_alignment_boosts_score():
    reports = [
        _report("fundamental_agent", 30),
        _report("valuation_agent", 20),
        _report("news_agent", 15),
        _report("sentiment_agent", 10),
        _report("macro_agent", 12),
    ]
    ctx = build_prior_context(reports)
    result = correlate_technical_with_context(
        technical_score=35,
        daily_bias="bullish",
        daily_rsi=45,
        ctx=ctx,
    )
    assert result.score_adjustment > 0
    assert any("aligns" in n.lower() for n in result.correlation_notes)


def test_narrative_divergence_flags_risk():
    reports = [
        _report("fundamental_agent", 10),
        _report("news_agent", -35),
        _report("sentiment_agent", -30),
    ]
    ctx = build_prior_context(reports)
    result = correlate_technical_with_context(
        technical_score=40,
        daily_bias="bullish",
        daily_rsi=55,
        ctx=ctx,
    )
    assert any("divergence" in n.lower() for n in result.correlation_notes)
    assert len(result.risks) >= 1


def test_oversold_fundamental_confluence():
    reports = [_report("fundamental_agent", 25), _report("valuation_agent", 18)]
    ctx = build_prior_context(reports)
    result = correlate_technical_with_context(
        technical_score=10,
        daily_bias="neutral",
        daily_rsi=28,
        ctx=ctx,
    )
    assert result.score_adjustment > 0
    assert any("oversold" in o.statement.lower() for o in result.opportunities)


def test_spy_correlation_note():
    reports = [
        _report(
            "market_dependency_agent",
            0,
            raw_data={"benchmark_correlations": [{"ticker": "SPY", "correlation": 0.8, "interpretation": "beta"}]},
        ),
        _report("macro_agent", -20),
    ]
    ctx = build_prior_context(reports)
    result = correlate_technical_with_context(
        technical_score=30,
        daily_bias="bullish",
        daily_rsi=50,
        ctx=ctx,
    )
    assert any("SPY" in n for n in result.correlation_notes)
