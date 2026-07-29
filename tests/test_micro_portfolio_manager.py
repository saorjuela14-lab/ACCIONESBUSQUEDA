"""Tests for micro portfolio capital desk."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from domain.enums import InvestmentRecommendation, StrategyType, TimeHorizon
from domain.reports import AgentReport, InvestmentThesis, ScenarioCase, StrategyConclusion
from services.committee_consensus import VOTING_AGENTS
from services.micro_portfolio_manager_service import MicroPortfolioManagerService


def _live_ohlcv(rows: int = 40, last_price: float = 1.5) -> pd.DataFrame:
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    idx = pd.date_range(end=end, periods=rows, freq="D", tz="UTC")
    close = [last_price] * rows
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1_000_000] * rows},
        index=idx,
    )


def _stale_ohlcv(rows: int = 40, last_price: float = 0.18) -> pd.DataFrame:
    end = datetime.now(timezone.utc) - timedelta(days=400)
    idx = pd.date_range(end=end, periods=rows, freq="D", tz="UTC")
    close = [last_price] * rows
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1000] * rows},
        index=idx,
    )


def _passing_thesis(ticker: str) -> InvestmentThesis:
    case = ScenarioCase(name="Base", probability=1.0, thesis="x", confidence=0.6)
    reports = [
        AgentReport(agent_name=n, ticker=ticker, score=22.0, confidence=0.7, summary="buy")
        for n in sorted(VOTING_AGENTS)
    ]
    strategies = [
        StrategyConclusion(
            strategy=StrategyType.MOMENTUM, score=20, confidence=0.7,
            conclusion="ok", horizon=TimeHorizon.WEEKLY,
        ),
        StrategyConclusion(
            strategy=StrategyType.SWING, score=20, confidence=0.7,
            conclusion="ok", horizon=TimeHorizon.WEEKLY,
        ),
        StrategyConclusion(
            strategy=StrategyType.BREAKOUT, score=20, confidence=0.7,
            conclusion="ok", horizon=TimeHorizon.INTRADAY,
        ),
        StrategyConclusion(
            strategy=StrategyType.VALUE, score=20, confidence=0.7,
            conclusion="ok", horizon=TimeHorizon.LONG_TERM,
        ),
        StrategyConclusion(
            strategy=StrategyType.GROWTH, score=20, confidence=0.7,
            conclusion="ok", horizon=TimeHorizon.LONG_TERM,
        ),
        StrategyConclusion(
            strategy=StrategyType.DIVIDEND, score=0, confidence=0.5,
            conclusion="n/a", horizon=TimeHorizon.LONG_TERM,
        ),
    ]
    return InvestmentThesis(
        ticker=ticker,
        executive_summary="ok",
        investment_thesis="ok",
        bull_case=case,
        bear_case=case,
        base_case=case,
        recommendation=InvestmentRecommendation.BUY,
        confidence=0.72,
        agent_reports=reports,
        strategy_conclusions=strategies,
    )


def _market_mock(prices: dict[str, float], *, stale: set[str] | None = None) -> MagicMock:
    stale = stale or set()
    market = MagicMock()

    async def quote(t: str):
        p = prices.get(t.upper())
        if p is None:
            return {"ticker": t, "company_name": f"Co {t}", "current_price": 50.0}
        return {"ticker": t, "company_name": f"Co {t}", "current_price": p}

    async def history(t: str, period: str = "3mo", interval: str = "1d"):
        p = prices.get(t.upper(), 50.0)
        if t.upper() in stale:
            return _stale_ohlcv(last_price=p)
        return _live_ohlcv(last_price=p)

    market.get_quote = AsyncMock(side_effect=quote)
    market.get_history = AsyncMock(side_effect=history)
    return market


def _analysis_pass() -> MagicMock:
    analysis = MagicMock()
    analysis.score_for_consensus = AsyncMock(side_effect=lambda t: _passing_thesis(t))
    analysis.score_for_micro_consensus = AsyncMock(side_effect=lambda t: _passing_thesis(t))
    return analysis


def _analysis_reject() -> MagicMock:
    """Strongly bearish — fails both strict and micro soft gates."""
    thesis = _passing_thesis("X")
    reports = [
        r.model_copy(update={"score": -40.0})
        for r in thesis.agent_reports
    ]
    strategies = [
        s.model_copy(update={"score": -40.0}) for s in thesis.strategy_conclusions
    ]
    thesis = thesis.model_copy(
        update={
            "agent_reports": reports,
            "strategy_conclusions": strategies,
            "recommendation": InvestmentRecommendation.STRONG_SELL,
        }
    )
    analysis = MagicMock()
    analysis.score_for_consensus = AsyncMock(return_value=thesis)
    analysis.score_for_micro_consensus = AsyncMock(return_value=thesis)
    return analysis


@pytest.mark.asyncio
async def test_micro_manager_builds_whole_share_plan():
    market = _market_mock({"F": 1.5, "NOK": 1.5, "SIRI": 1.5, "T": 1.5})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery, analysis_service=_analysis_pass())
    plan = await svc.manage(capital=22)

    assert plan.capital == 22
    assert plan.max_share_price is not None
    assert plan.max_share_price <= 12
    assert len(plan.lines) >= 1
    assert len(plan.lines) <= 1  # ultra-micro: one position
    for line in plan.lines:
        assert line.shares >= 1
        assert line.price <= plan.max_share_price
        assert line.allocation_pct <= 40.5
    assert all(p.committee_unanimous for p in plan.picks)
    deployed = sum(l.allocation_usd for l in plan.lines)
    assert deployed / 22 <= 0.85
    assert "comité" in plan.summary.lower() or "consenso" in plan.summary.lower()


@pytest.mark.asyncio
async def test_micro_manager_respects_price_cap():
    market = _market_mock({})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery, analysis_service=_analysis_pass())
    plan = await svc.manage(capital=22)
    assert plan.lines == [] or all(l.price <= plan.max_share_price for l in plan.lines)


@pytest.mark.asyncio
async def test_micro_manager_skips_delisted_even_with_quote():
    market = _market_mock(
        {"NKLA": 0.18, "F": 1.4, "NOK": 2.0},
        stale={"NKLA"},
    )
    from domain.discovery import DiscoveryCandidate

    discovery = MagicMock()
    discovery.research = AsyncMock(
        return_value=MagicMock(
            candidates=[
                DiscoveryCandidate(
                    ticker="NKLA",
                    company_name="Nikola",
                    score=90,
                    rationale="stale seed",
                    news_headlines=[],
                    sources=["test"],
                )
            ]
        )
    )

    svc = MicroPortfolioManagerService(market, discovery, analysis_service=_analysis_pass())
    plan = await svc.manage(capital=22)
    tickers = {l.ticker for l in plan.lines}
    assert "NKLA" not in tickers
    assert tickers


@pytest.mark.asyncio
async def test_micro_manager_enforces_cash_reserve_not_full_deploy():
    market = _market_mock({"F": 0.5, "NOK": 0.5, "SIRI": 0.5, "T": 0.5})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery, analysis_service=_analysis_pass())
    plan = await svc.manage(capital=22)
    deployed = sum(l.allocation_usd for l in plan.lines)
    cash_pct = (22 - deployed) / 22 * 100
    assert cash_pct >= 15
    assert any("Política de riesgo" in w for w in plan.warnings)


@pytest.mark.asyncio
async def test_micro_manager_empty_without_committee_consensus():
    market = _market_mock({"F": 1.5, "NOK": 1.5})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery, analysis_service=_analysis_reject())
    plan = await svc.manage(capital=22)
    assert plan.lines == []
    assert plan.picks == []
    assert any(
        "consenso" in w.lower() or "comité" in w.lower() or "caza" in w.lower() or "mayoría" in w.lower()
        for w in plan.warnings
    )


@pytest.mark.asyncio
async def test_micro_manager_empty_without_analysis_service():
    market = _market_mock({"F": 1.5})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))
    svc = MicroPortfolioManagerService(market, discovery)
    plan = await svc.manage(capital=22)
    assert plan.lines == []
    assert any(
        "Comité no disponible" in w or "caza" in w.lower() or "consenso" in w.lower()
        for w in plan.warnings
    )
