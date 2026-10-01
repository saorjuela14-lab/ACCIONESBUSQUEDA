"""Net deposited capital from Alpaca transfer activities — reporting P&L base."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.deposited_capital_service import (
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
async def test_get_deposited_base_from_alpaca(monkeypatch):
    from config.settings import get_settings

    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
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
    assert snap.source == "alpaca"
    assert snap.amount == 21.74
    assert snap.deposits == 21.74
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_get_deposited_base_uses_ttl_cache():
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
        first = await get_deposited_base(force=True)
        second = await get_deposited_base()
    assert first.amount == 21.74
    assert second.source == "alpaca"
    # CSD/CSW/JNLC/TRANS × (non_trade, then maybe None if empty)
    assert broker.list_account_activities.await_count >= 4


@pytest.mark.asyncio
async def test_fallback_to_env_never_silent_twenty(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("DEPOSITED_BASE_USD", "21.74")
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.list_account_activities = AsyncMock(side_effect=RuntimeError("alpaca down"))
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        snap = await get_deposited_base(force=True)
    assert snap.source == "env"
    assert snap.amount == 21.74
    assert snap.amount != 20.0
    get_settings.cache_clear()
    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_unavailable_does_not_invent_twenty(monkeypatch):
    from config.settings import get_settings

    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()
    broker = MagicMock()
    broker.is_configured.return_value = False
    with patch(
        "services.deposited_capital_service.get_broker_provider",
        return_value=broker,
    ):
        snap = await get_deposited_base(force=True)
    assert snap.source.startswith("unavailable")
    assert snap.amount is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stale_cache_beats_env_when_alpaca_fails(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("DEPOSITED_BASE_USD", "99.00")
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
        warm = await get_deposited_base(force=True)
        assert warm.amount == 21.74
        broker.list_account_activities = AsyncMock(side_effect=RuntimeError("down"))
        snap = await get_deposited_base(force=True)
    assert snap.source == "cache"
    assert snap.amount == 21.74
    get_settings.cache_clear()
    monkeypatch.delenv("DEPOSITED_BASE_USD", raising=False)
    get_settings.cache_clear()
