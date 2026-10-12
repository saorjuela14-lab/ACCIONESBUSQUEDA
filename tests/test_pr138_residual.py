"""PR #138 residual: heartbeat loop/timeout, exclusive-until ties, sanitize lease errors."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.broker import BrokerOrderResult
from services.alpaca_order_service import AlpacaOrderService, order_page_cursor_ts
from services.db_lease import LeaseHeartbeat


@pytest.mark.asyncio
async def test_heartbeat_loop_marks_lost_and_alerts_on_exception():
    hb = LeaseHeartbeat(
        name="live_stocks",
        owner="host:1:abc",
        interval_seconds=0.01,
        beat_timeout_seconds=1.0,
    )
    n = {"i": 0}

    async def beat() -> bool:
        n["i"] += 1
        if n["i"] == 1:
            return True
        raise ConnectionError("db down mid-loop")

    hb._beat = beat
    with patch.object(hb, "_alert_lost", AsyncMock()) as alert:
        await hb.start()
        for _ in range(80):
            if hb.lost:
                break
            await asyncio.sleep(0.01)
        assert hb.lost is True
        alert.assert_awaited()
        detail = str(alert.await_args.args[0] if alert.await_args.args else alert.await_args.kwargs.get("detail") or "")
        if not detail:
            detail = str(alert.await_args.args[0]) if alert.await_args.args else ""
        # _alert_lost(detail) positional
        assert "loop" in str(alert.await_args.args[0]).lower() or "error" in str(alert.await_args.args[0]).lower()
        assert hb._task is None or hb._task.done()
    await hb.stop()


@pytest.mark.asyncio
async def test_still_mine_timeout_fail_closed_and_alerts():
    hb = LeaseHeartbeat(
        name="live_stocks",
        owner="host:1:abc",
        beat_timeout_seconds=0.05,
    )

    async def hang() -> bool:
        await asyncio.sleep(2)
        return True

    hb._beat = hang
    with patch.object(hb, "_alert_lost", AsyncMock()) as alert:
        assert await hb.still_mine() is False
    assert hb.lost is True
    alert.assert_awaited()
    assert "timeout" in str(alert.await_args.args[0]).lower()


@pytest.mark.asyncio
async def test_heartbeat_start_timeout_fail_closed_and_alerts():
    hb = LeaseHeartbeat(
        name="live_stocks",
        owner="host:1:abc",
        beat_timeout_seconds=0.05,
    )

    async def hang() -> bool:
        await asyncio.sleep(2)
        return True

    hb._beat = hang
    with patch.object(hb, "_alert_lost", AsyncMock()) as alert:
        await hb.start()
    assert hb.lost is True
    assert hb._task is None
    alert.assert_awaited()
    assert "timeout" in str(alert.await_args.args[0]).lower()


def test_order_page_cursor_prefers_submitted_at():
    od = BrokerOrderResult(
        id="x",
        symbol="SNAP",
        qty=1,
        side="sell",
        type="stop",
        status="held",
        raw={
            "created_at": "2026-10-02T12:00:00Z",
            "submitted_at": "2026-10-02T12:01:00Z",
        },
    )
    assert order_page_cursor_ts(od) == "2026-10-02T12:01:00Z"


@pytest.mark.asyncio
async def test_find_working_stop_keeps_same_timestamp_tie():
    """S2: exclusive until must not drop leftover orders that share the tail ts."""
    base = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    tie = "2026-10-02T11:00:00Z"
    rows: list[BrokerOrderResult] = []
    for i in range(190):
        ts = (base - timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(
            BrokerOrderResult(
                id=f"new-{i}",
                symbol="AAPL",
                qty=1,
                side="sell",
                type="limit",
                status="new",
                raw={"submitted_at": ts, "created_at": ts},
            )
        )
    for i in range(30):
        oid = "snap-stop" if i == 20 else f"tie-{i}"
        rows.append(
            BrokerOrderResult(
                id=oid,
                symbol="SNAP" if i == 20 else "AAPL",
                qty=1,
                side="sell",
                type="stop" if i == 20 else "limit",
                status="held" if i == 20 else "new",
                raw={"submitted_at": tie, "created_at": tie, "stop_price": "5.48"},
            )
        )

    def _exclusive(until: str | None, limit: int) -> list[BrokerOrderResult]:
        if until:
            out = [o for o in rows if order_page_cursor_ts(o) < until]
        else:
            out = list(rows)
        return out[:limit]

    inner = MagicMock()
    svc = AlpacaOrderService(broker=inner)
    listed = AsyncMock(side_effect=lambda status="all", limit=50, after=None, until=None: _exclusive(until, limit))
    with patch.object(svc, "list_orders", listed):
        found = await svc.find_working_stop("SNAP")
    assert found is not None
    assert found.id == "snap-stop"
    assert any((c.kwargs.get("limit") == 500) for c in listed.await_args_list)


@pytest.mark.asyncio
async def test_find_working_stop_uses_submitted_at_not_earlier_created_at():
    """S5: created_at 60s before submitted_at at the page edge must not skip the stop."""
    edge_sub = "2026-10-02T12:01:00Z"
    edge_created = "2026-10-02T12:00:00Z"
    stop_ts = "2026-10-02T12:00:30Z"
    page1 = [
        BrokerOrderResult(
            id=f"pad-{i}",
            symbol="AAPL",
            qty=1,
            side="sell",
            type="limit",
            status="new",
            raw={"submitted_at": "2026-10-02T13:00:00Z", "created_at": "2026-10-02T13:00:00Z"},
        )
        for i in range(199)
    ] + [
        BrokerOrderResult(
            id="edge",
            symbol="AAPL",
            qty=1,
            side="sell",
            type="limit",
            status="new",
            raw={"submitted_at": edge_sub, "created_at": edge_created},
        )
    ]
    stop = BrokerOrderResult(
        id="snap-stop",
        symbol="SNAP",
        qty=1,
        side="sell",
        type="stop",
        status="held",
        raw={"submitted_at": stop_ts, "created_at": stop_ts, "stop_price": "5.48"},
    )

    async def _list(status="all", limit=50, after=None, until=None):
        if until == edge_created:
            return []
        if until == edge_sub:
            return [stop]
        return page1

    inner = MagicMock()
    svc = AlpacaOrderService(broker=inner)
    with patch.object(svc, "list_orders", AsyncMock(side_effect=_list)):
        found = await svc.find_working_stop("SNAP")
    assert found is not None
    assert found.id == "snap-stop"


@pytest.mark.asyncio
async def test_lease_status_error_redacts_host_ip():
    from database.url import sanitize_db_error
    from apis.routes.ops import _lease_status

    err = ConnectionError("could not connect to 127.0.0.1:5999 host=ep-small-pond.aws.neon.tech")
    cleaned = sanitize_db_error(err)
    assert "127.0.0.1" not in cleaned
    assert "5999" not in cleaned or "[redacted-host]" in cleaned
    assert "neon.tech" not in cleaned
    assert "[redacted-host]" in cleaned

    session = MagicMock()
    with patch(
        "services.db_lease.snapshot_leases",
        AsyncMock(side_effect=ConnectionError("timeout 10.1.2.3:5432")),
    ):
        body = await _lease_status(session)
    assert "10.1.2.3" not in body["error"]
    assert "[redacted-host]" in body["error"]


@pytest.mark.asyncio
async def test_snapshot_leases_error_redacts_host():
    from services.db_lease import snapshot_leases

    session = MagicMock()
    with patch(
        "services.db_lease.snapshot_lease",
        AsyncMock(side_effect=OSError("connection to 192.168.1.8:5432 failed")),
    ):
        out = await snapshot_leases(session, names=["live_stocks"])
    assert "192.168.1.8" not in out["live_stocks"]["error"]
    assert "[redacted-host]" in out["live_stocks"]["error"]
