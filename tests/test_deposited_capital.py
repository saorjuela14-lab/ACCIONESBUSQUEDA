"""Net deposited capital from Alpaca transfer activities — reporting P&L base."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.deposited_capital_service import (
    CANONICAL_SOURCE,
    MISSING_SOURCE,
    deposited_base_status,
    get_deposited_base,
    net_transfers_from_activities,
    reset_deposited_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_deposited_cache()
    yield
    reset_deposited_cache()


def test_net_transfers_csd_minus_csw():
    rows = [
        {"activity_type": "CSD", "net_amount": "21.74", "status": "executed", "id": "1"},
        {"activity_type": "CSW", "net_amount": "1.00", "status": "executed", "id": "2"},
    ]
    net, deposits, withdrawals, counted = net_transfers_from_activities(rows)
    assert deposits == 21.74
    assert withdrawals == 1.0
    assert net == 20.74
    assert counted == 2


def test_net_transfers_jnlc_and_skip_canceled():
    rows = [
        {"activity_type": "JNLC", "net_amount": "21.74", "status": "executed"},
        {"activity_type": "CSD", "net_amount": "50", "status": "canceled"},
        {"activity_type": "FILL", "net_amount": "5", "status": "executed"},
    ]
    net, deposits, withdrawals, counted = net_transfers_from_activities(rows)
    assert net == 21.74
    assert deposits == 21.74
    assert withdrawals == 0.0
    assert counted == 1


def test_oct_qty_when_net_amount_missing():
    rows = [{"activity_type": "OCT", "qty": "21.74", "symbol": "USDTUSD", "status": "executed"}]
    net, deposits, withdrawals, counted = net_transfers_from_activities(rows)
    assert net == 21.74
    assert deposits == 21.74
    assert counted == 1


def test_oct_onchain_deposit_counts():
    rows = [{"activity_type": "OCT", "net_amount": "21.74", "status": "executed"}]
    net, deposits, withdrawals, counted = net_transfers_from_activities(rows)
    assert net == 21.74
    assert deposits == 21.74
    assert withdrawals == 0.0
    assert counted == 1


def test_csw_negative_net_amount_not_double_flipped():
    rows = [{"activity_type": "CSW", "net_amount": "-2.50", "status": "executed"}]
    net, _dep, withdrawals, _n = net_transfers_from_activities(rows)
    assert withdrawals == 2.5
    assert net == -2.5


@pytest.mark.asyncio
async def test_canonical_env_21_76_floor_and_not_alpaca(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("DEPOSITED_BASE_USD", "21.76")
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.list_account_activities = AsyncMock(
        return_value=[
            {"id": "a1", "activity_type": "CSD", "net_amount": "21.74", "status": "executed"},
        ]
    )
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        snap = await get_deposited_base(force=True)
    assert snap.source == CANONICAL_SOURCE
    assert snap.amount == 21.76
    assert snap.floor_5pct == 20.67
    assert snap.buy_allowed is True
    assert snap.alpaca_amount == 21.74
    assert any("discrepancy" in w and "alpaca" in w for w in snap.warnings)
    payload = deposited_base_status(snap)
    assert payload["amount"] == 21.76
    assert payload["source"] == CANONICAL_SOURCE
    assert payload["floor_5pct"] == 20.67
    get_settings.cache_clear()
    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_missing_env_fail_closed_for_buys(monkeypatch):
    from config.settings import get_settings

    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.list_account_activities = AsyncMock(
        return_value=[
            {"id": "a1", "activity_type": "CSD", "net_amount": "21.76", "status": "executed"},
        ]
    )
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        snap = await get_deposited_base(force=True)
    assert snap.source == MISSING_SOURCE
    assert snap.amount is None
    assert snap.buy_allowed is False
    assert snap.amount != 20.0
    assert any("fail-closed" in w for w in snap.warnings)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_portfolio_initial_discrepancy_warning(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("DEPOSITED_BASE_USD", "21.76")
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = False
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        snap = await get_deposited_base(force=True, portfolio_initial=20.0)
    assert snap.amount == 21.76
    assert snap.portfolio_initial == 20.0
    assert any("initial_capital" in w for w in snap.warnings)
    get_settings.cache_clear()
    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_get_deposited_base_uses_ttl_cache(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("DEPOSITED_BASE_USD", "21.76")
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = False
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        first = await get_deposited_base(force=True)
        second = await get_deposited_base()
    assert first.amount == 21.76
    assert second.source == CANONICAL_SOURCE
    get_settings.cache_clear()
    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_resolve_trading_base_is_same_function(monkeypatch):
    from config.settings import get_settings
    from services.deposited_capital_service import resolve_trading_base

    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = False
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        snap = await resolve_trading_base(equity=21.01)
    assert snap.amount is None
    assert snap.buy_allowed is False
    assert snap.source == MISSING_SOURCE
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ops_status_exposes_deposited_base_and_warning(monkeypatch):
    from types import SimpleNamespace
    from services.deposited_capital_service import DepositedBase
    from apis.routes import ops as ops_routes

    monkeypatch.setenv("DEPOSITED_BASE_USD", "21.76")
    from config.settings import get_settings

    get_settings.cache_clear()
    snap = DepositedBase(
        amount=21.76,
        source=CANONICAL_SOURCE,
        floor_5pct=20.67,
        buy_allowed=True,
        warnings=("discrepancy alpaca=21.74 vs env:DEPOSITED_BASE_USD=21.76",),
        alpaca_amount=21.74,
        portfolio_initial=20.0,
    )
    session = MagicMock()
    ks = SimpleNamespace(model_dump=lambda mode="json": {"active": False})
    auto = SimpleNamespace(
        can_auto_trade_async=AsyncMock(return_value=(False, "live_entries_disabled")),
        policy=lambda: SimpleNamespace(model_dump=lambda mode="json": {}),
    )
    flags = SimpleNamespace(get_json=AsyncMock(return_value={}))
    with (
        patch("apis.routes.ops.get_settings", return_value=get_settings()),
        patch("apis.routes.ops.KillSwitchService", return_value=SimpleNamespace(status=AsyncMock(return_value=ks))),
        patch("apis.routes.ops.AutoExecuteService", return_value=auto),
        patch("apis.routes.ops.OpsFlagRepository", return_value=flags),
        patch("services.deposited_capital_service.get_deposited_base", AsyncMock(return_value=snap)),
        patch(
            "database.repositories.portfolio_repository.PortfolioRepository.list_all",
            AsyncMock(return_value=[SimpleNamespace(initial_capital=20.0)]),
        ),
    ):
        # ops_status imports get_deposited_base inside the function
        body = await ops_routes.ops_status(session)
    assert body["deposited_base"]["amount"] == 21.76
    assert body["deposited_base"]["source"] == CANONICAL_SOURCE
    assert body["deposited_base"]["floor_5pct"] == 20.67
    assert body["deposited_base"]["buy_allowed"] is True
    assert body["warnings"]
    assert any("discrepancy" in w for w in body["warnings"])
    get_settings.cache_clear()
    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()
