"""Tests for micro portfolio capital desk."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

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


@pytest.mark.asyncio
async def test_micro_manager_builds_whole_share_plan():
    market = _market_mock({"SOUN": 1.5, "PLUG": 1.5, "FCEL": 1.5, "RIOT": 1.5})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery)
    plan = await svc.manage(capital=22)

    assert plan.capital == 22
    assert plan.max_share_price <= 5
    assert len(plan.lines) >= 1
    for line in plan.lines:
        assert line.shares >= 1
        assert line.price <= plan.max_share_price
        assert line.allocation_usd <= 22
        # Firm max_position_pct default 35%
        assert line.allocation_pct <= 35.5
    deployed = sum(l.allocation_usd for l in plan.lines)
    assert deployed / 22 <= 0.85  # not ~100%; micro cash floor 20%
    assert "Plan de gestión" in plan.summary or "desplegar" in plan.summary.lower()


@pytest.mark.asyncio
async def test_micro_manager_respects_price_cap():
    market = _market_mock({})  # all seeds → $50
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery)
    plan = await svc.manage(capital=22)
    assert plan.lines == [] or all(l.price <= plan.max_share_price for l in plan.lines)


@pytest.mark.asyncio
async def test_micro_manager_skips_delisted_even_with_quote():
    """NKLA-style residual quote must not enter the plan."""
    market = _market_mock(
        {"NKLA": 0.18, "SOUN": 1.4, "PLUG": 2.0},
        stale={"NKLA"},
    )
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    # Temporarily inject NKLA into seed probe by excluding nothing; NKLA is no longer
    # in the seed list, so call gather with a discovery candidate that is stale.
    from domain.discovery import DiscoveryCandidate

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

    svc = MicroPortfolioManagerService(market, discovery)
    plan = await svc.manage(capital=22)
    tickers = {l.ticker for l in plan.lines}
    assert "NKLA" not in tickers
    assert tickers  # still finds live SOUN/PLUG


@pytest.mark.asyncio
async def test_micro_manager_enforces_cash_reserve_not_full_deploy():
    market = _market_mock({"SOUN": 0.5, "PLUG": 0.5, "FCEL": 0.5, "RIOT": 0.5})
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    svc = MicroPortfolioManagerService(market, discovery)
    plan = await svc.manage(capital=22)
    deployed = sum(l.allocation_usd for l in plan.lines)
    cash_pct = (22 - deployed) / 22 * 100
    assert cash_pct >= 15  # micro floor 20% with share rounding slack
    assert any("Política de riesgo" in w for w in plan.warnings)
