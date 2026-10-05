"""PR #137 follow-up: fail-closed lease, no-channel alerts, after/until orders, db_host."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.db_lease import LEASE_LIFECYCLE, LeaseHeartbeat
from services.desk_ops_alert import KIND_LEASE_MISSES, emit_desk_ops_alert, reset_desk_ops_alert_dedupe


@pytest.mark.asyncio
async def test_still_mine_fail_closed_on_db_error():
    hb = LeaseHeartbeat(name="live_stocks", owner="host:1:abc")
    hb._beat = AsyncMock(side_effect=TimeoutError("db timeout"))
    with patch.object(hb, "_alert_lost", AsyncMock()) as alert:
        assert await hb.still_mine() is False
    assert hb.lost is True
    alert.assert_awaited()


@pytest.mark.asyncio
async def test_heartbeat_start_fail_closed_on_db_error():
    hb = LeaseHeartbeat(name="live_stocks", owner="host:1:abc")
    hb._beat = AsyncMock(side_effect=RuntimeError("pool timeout"))
    with patch.object(hb, "_alert_lost", AsyncMock()) as alert:
        await hb.start()
    assert hb.lost is True
    assert hb._task is None
    alert.assert_awaited()


@pytest.mark.asyncio
async def test_emit_desk_ops_alert_false_when_no_channel():
    reset_desk_ops_alert_dedupe()
    push = MagicMock()
    push.any_channel_configured = False
    push.notify_message = AsyncMock()
    with patch("services.push_notification_service.PushNotificationService", return_value=push):
        ok = await emit_desk_ops_alert(KIND_LEASE_MISSES, owner="h", detail="misses=2")
    assert ok is False
    push.notify_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_alert_dedupe_stable_across_cycles():
    from services.desk_ops_alert import _dedupe_key

    reset_desk_ops_alert_dedupe()
    key = "lease_misses:live_stocks_stable"
    assert _dedupe_key(
        KIND_LEASE_MISSES,
        owner="holder-a",
        detail="lease=live_stocks misses=2 expires=t1",
        dedupe_key=key,
    ) == key
    assert _dedupe_key(
        KIND_LEASE_MISSES,
        owner="holder-a",
        detail="lease=live_stocks misses=5 expires=t2",
        dedupe_key=key,
    ) == key
    push = MagicMock()
    push.any_channel_configured = True
    push.notify_message = AsyncMock(return_value={"telegram": True})
    with patch("services.push_notification_service.PushNotificationService", return_value=push):
        a = await emit_desk_ops_alert(
            KIND_LEASE_MISSES,
            owner="holder-a",
            detail="lease=live_stocks misses=2 expires=t1",
            dedupe_key=key,
            force=True,
        )
        b = await emit_desk_ops_alert(
            KIND_LEASE_MISSES,
            owner="holder-a",
            detail="lease=live_stocks misses=5 expires=t2",
            dedupe_key=key,
        )
    assert a is True
    assert b is False
    assert push.notify_message.await_count == 1


@pytest.mark.asyncio
async def test_list_orders_uses_until_not_page_token():
    from providers.broker.alpaca_provider import AlpacaBrokerProvider

    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=True)
    broker._request = AsyncMock(return_value=[])
    await broker.list_orders(status="all", limit=200, until="2026-10-02T12:00:00Z")
    params = broker._request.await_args.kwargs.get("params") or {}
    assert "page_token" not in params
    assert params.get("until") == "2026-10-02T12:00:00Z"
    assert params.get("limit") == 200


@pytest.mark.asyncio
async def test_lifecycle_scan_starts_lease_heartbeat():
    from services.scheduler_service import SchedulerService

    settings = MagicMock()
    settings.lifecycle_enabled = True
    settings.lifecycle_auto_exit = False
    settings.push_daily_trades = False
    svc = SchedulerService.__new__(SchedulerService)
    svc._settings = settings

    snap = MagicMock(acquired=True, owner="me", misses_consecutive=0)
    hb = MagicMock()
    hb.lost = False
    hb.start = AsyncMock()
    hb.still_mine = AsyncMock(return_value=True)
    hb.stop = AsyncMock()

    session = MagicMock()
    life = MagicMock()
    life.scan = AsyncMock(return_value=MagicMock(positions=0, exits=[], warnings=[]))

    async def _sessions():
        yield session

    with (
        patch("services.scheduler_service.should_run_automation", return_value=True),
        patch("services.scheduler_service.get_session", _sessions),
        patch("services.db_lease.acquire_lease", AsyncMock(return_value=snap)),
        patch("services.db_lease.release_lease", AsyncMock()),
        patch("services.db_lease.run_owner", return_value="me"),
        patch("services.db_lease.LeaseHeartbeat", return_value=hb),
        patch("services.position_lifecycle_service.PositionLifecycleService", return_value=life),
    ):
        await svc._run_lifecycle_scan()
    hb.start.assert_awaited()
    hb.still_mine.assert_awaited()
    hb.stop.assert_awaited()
    life.scan.assert_awaited()


@pytest.mark.asyncio
async def test_health_and_ops_status_omit_db_host():
    from httpx import ASGITransport, AsyncClient

    from apis.app import create_app
    from apis.routes import ops as ops_mod

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
    assert health.status_code == 200
    assert "db_host" not in health.json()

    with (
        patch.object(ops_mod, "_lease_status", AsyncMock(return_value={})),
        patch("services.kill_switch_service.KillSwitchService.status", new=AsyncMock(return_value=MagicMock(model_dump=lambda mode="json": {}))),
        patch("services.auto_execute_service.AutoExecuteService.can_auto_trade_async", new=AsyncMock(return_value=(False, "x"))),
        patch("services.auto_execute_service.AutoExecuteService.policy", return_value=MagicMock(model_dump=lambda mode="json": {})),
        patch("database.repositories.ops_repository.OpsFlagRepository.get_json", new=AsyncMock(return_value={})),
        patch("services.deposited_capital_service.get_deposited_base", new=AsyncMock(return_value=MagicMock())),
        patch(
            "services.deposited_capital_service.deposited_base_status",
            return_value={"warnings": []},
        ),
    ):
        body = await ops_mod.ops_status(session=MagicMock())
    assert "db_host" not in body
