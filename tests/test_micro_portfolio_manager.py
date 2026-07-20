"""Tests for micro portfolio capital desk."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.micro_portfolio_manager_service import MicroPortfolioManagerService


@pytest.mark.asyncio
async def test_micro_manager_builds_whole_share_plan():
    market = MagicMock()
    market.get_quote = AsyncMock(side_effect=lambda t: {
        "ticker": t,
        "company_name": f"Co {t}",
        "current_price": 1.5 if t in ("SOUN", "PLUG", "FCEL") else 50.0,
    })

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
    assert "Plan de gestión" in plan.summary or "desplegar" in plan.summary.lower()


@pytest.mark.asyncio
async def test_micro_manager_respects_price_cap():
    market = MagicMock()
    market.get_quote = AsyncMock(return_value={
        "ticker": "EXP",
        "company_name": "Expensive",
        "current_price": 80.0,
    })
    discovery = MagicMock()
    discovery.research = AsyncMock(return_value=MagicMock(candidates=[]))

    # Force seed list to only expensive quotes → empty lines
    svc = MicroPortfolioManagerService(market, discovery)
    svc_module_seeds = __import__(
        "services.micro_portfolio_manager_service", fromlist=["_MICRO_SEED_TICKERS"]
    )
    # All seeds return 80 → no affordable lines
    plan = await svc.manage(capital=22)
    assert plan.lines == [] or all(l.price <= plan.max_share_price for l in plan.lines)
