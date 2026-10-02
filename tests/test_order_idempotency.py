"""Order idempotency (422/timeout/attempt) and live-stop detection (held + qty)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from domain.broker import BrokerOrderRequest, BrokerOrderResult
from services.live_cycle_lock import allocate_live_client_order_id, live_client_order_id
from services.order_idempotency import (
    ALPACA_DUPLICATE_COID_CODE,
    ALPACA_INSUFFICIENT_QTY_CODE,
    FLAG_ATTEMPTS,
    bump_attempt,
    is_duplicate_client_order_id_error,
    is_insufficient_qty_error,
    is_timeout_or_network,
    is_unrelated_422,
    order_is_live_stop,
    persist_attempt,
    read_attempt,
)


def _http_err(status: int, *, code: int | None = None, message: str = "nope") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.alpaca.markets/v2/orders")
    resp = httpx.Response(
        status,
        json={"code": code, "message": message} if code is not None else {"message": message},
        request=req,
    )
    err = httpx.HTTPStatusError(f"Alpaca {status}: {message}", request=req, response=resp)
    err.alpaca_code = code
    err.alpaca_status = status
    return err


def test_422_duplicate_vs_other_cause():
    dup = _http_err(422, code=ALPACA_DUPLICATE_COID_CODE, message="client_order_id must be unique")
    other = _http_err(422, code=40010000, message="qty must be > 0")
    generic = _http_err(422, code=ALPACA_DUPLICATE_COID_CODE, message="qty must be integer")
    assert is_duplicate_client_order_id_error(dup) is True
    assert is_unrelated_422(dup) is False
    assert is_duplicate_client_order_id_error(other) is False
    assert is_unrelated_422(other) is True
    assert is_duplicate_client_order_id_error(generic) is False
    assert is_unrelated_422(generic) is True
    assert is_duplicate_client_order_id_error(RuntimeError("nope")) is False


def test_timeout_classifier():
    assert is_timeout_or_network(httpx.ReadTimeout("t")) is True
    assert is_timeout_or_network(httpx.ConnectError("down")) is True
    assert is_timeout_or_network(_http_err(500, message="boom")) is False


def test_held_bracket_stop_is_live():
    held = SimpleNamespace(
        symbol="SNAP",
        side="sell",
        type="stop",
        status="held",
        raw={"stop_price": "5.48"},
    )
    assert order_is_live_stop(held) is True
    assert order_is_live_stop(SimpleNamespace(symbol="SNAP", side="sell", type="stop", status="open", raw={})) is False
    assert order_is_live_stop({"side": "sell", "type": "stop", "status": "new", "stop_price": "5"}) is True


@pytest.mark.asyncio
async def test_attempt_persisted_in_db_flag_not_memory():
    flags = MagicMock()
    store: dict = {}

    async def _get(name):
        return dict(store.get(name) or {})

    async def _set(name, val):
        store[name] = dict(val)

    flags.get_json = AsyncMock(side_effect=_get)
    flags.set_json = AsyncMock(side_effect=_set)
    now = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    cid, n = await allocate_live_client_order_id(flags, "SNAP", "buy", when=now)
    assert n == 1
    assert cid.endswith("-1")
    await persist_attempt(flags, "SNAP:buy:20261002", 1)
    n2 = await bump_attempt(flags, "SNAP:buy:20261002")
    assert n2 == 2
    assert store[FLAG_ATTEMPTS]["SNAP:buy:20261002"] == 2
    assert await read_attempt(flags, "SNAP:buy:20261002") == 2
    cid2, n3 = await allocate_live_client_order_id(flags, "SNAP", "buy", when=now)
    assert n3 == 2
    assert cid2 == "live-SNAP-20261002-buy-2"
    assert cid2 != cid


@pytest.mark.asyncio
async def test_allocate_bumps_after_canceled_order():
    flags = MagicMock()
    store = {FLAG_ATTEMPTS: {"SNAP:buy:20261002": 1}}

    async def _get(name):
        return dict(store.get(name) or {})

    async def _set(name, val):
        store[name] = dict(val)

    flags.get_json = AsyncMock(side_effect=_get)
    flags.set_json = AsyncMock(side_effect=_set)
    broker = MagicMock()
    broker.get_order_by_client_order_id = AsyncMock(
        return_value={"id": "old", "status": "canceled", "client_order_id": "live-SNAP-20261002-buy-1"}
    )
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    cid, n = await allocate_live_client_order_id(flags, "SNAP", "buy", when=now, broker=broker)
    assert n == 2
    assert cid == "live-SNAP-20261002-buy-2"


@pytest.mark.asyncio
async def test_submit_one_422_duplicate_reconciles_without_new_fill():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = "rid"
    inner.submit_order = AsyncMock(
        side_effect=_http_err(422, code=ALPACA_DUPLICATE_COID_CODE, message="client_order_id must be unique")
    )
    inner.get_order_by_client_order_id = AsyncMock(
        return_value={
            "id": "ord-1",
            "symbol": "SNAP",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "status": "filled",
            "filled_qty": "1",
            "filled_avg_price": "6.12",
            "client_order_id": "live-SNAP-20261002-buy-1",
        }
    )
    svc = AlpacaOrderService(broker=inner)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out = await svc.submit_one(
            BrokerOrderRequest(
                symbol="SNAP",
                qty=1,
                side="buy",
                client_order_id="live-SNAP-20261002-buy-1",
                source_tag="desk",
            )
        )
    assert out.id == "ord-1"
    assert out.error is None
    assert out.raw.get("reconciled") is True
    assert out.raw.get("no_new_fill") is True
    inner.submit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_one_timeout_existing_reconciles_missing_retries_same_id():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = "rid"
    inner.submit_order = AsyncMock(side_effect=httpx.ReadTimeout("no reply"))
    inner.get_order_by_client_order_id = AsyncMock(
        return_value={
            "id": "ord-2",
            "symbol": "SNAP",
            "qty": "1",
            "side": "buy",
            "status": "accepted",
            "client_order_id": "live-SNAP-20261002-buy-1",
        }
    )
    svc = AlpacaOrderService(broker=inner)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out = await svc.submit_one(
            BrokerOrderRequest(
                symbol="SNAP", qty=1, side="buy", client_order_id="live-SNAP-20261002-buy-1"
            )
        )
    assert out.raw.get("reconciled") is True
    assert out.id == "ord-2"
    inner.submit_order.assert_awaited_once()

    inner2 = MagicMock()
    inner2.paper = True
    inner2.last_request_id = "rid"
    inner2.submit_order = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("no reply"),
            {"id": "ord-3", "symbol": "SNAP", "qty": "1", "side": "buy", "status": "accepted",
             "client_order_id": "live-SNAP-20261002-buy-1"},
        ]
    )
    inner2.get_order_by_client_order_id = AsyncMock(return_value=None)
    svc2 = AlpacaOrderService(broker=inner2)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out2 = await svc2.submit_one(
            BrokerOrderRequest(
                symbol="SNAP", qty=1, side="buy", client_order_id="live-SNAP-20261002-buy-1"
            )
        )
    assert out2.id == "ord-3"
    assert not (out2.raw or {}).get("reconciled")
    assert inner2.submit_order.await_count == 2
    first_cid = inner2.submit_order.await_args_list[0].args[0]["client_order_id"]
    second_cid = inner2.submit_order.await_args_list[1].args[0]["client_order_id"]
    assert first_cid == second_cid == "live-SNAP-20261002-buy-1"


@pytest.mark.asyncio
async def test_retry_after_cancel_uses_attempt_plus_one_and_reruns_gates():
    from services.alpaca_order_service import AlpacaOrderService

    flags = MagicMock()
    store = {FLAG_ATTEMPTS: {"AAPL:buy:20261002": 1}}

    async def _get(name):
        return dict(store.get(name) or {})

    async def _set(name, val):
        store[name] = dict(val)

    flags.get_json = AsyncMock(side_effect=_get)
    flags.set_json = AsyncMock(side_effect=_set)
    broker = MagicMock()
    broker.get_order_by_client_order_id = AsyncMock(
        return_value={"status": "rejected", "id": "old"}
    )
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    cid, n = await allocate_live_client_order_id(flags, "AAPL", "buy", when=now, broker=broker)
    assert n == 2
    assert cid == live_client_order_id("AAPL", "buy", when=now, attempt=2)

    inner = MagicMock()
    inner.paper = False
    inner.last_request_id = None
    inner.submit_order = AsyncMock(side_effect=AssertionError("gates must block"))
    svc = AlpacaOrderService(broker=inner)
    svc._live_buy_central_gates = AsyncMock(return_value="kill_switch_entries_blocked")
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ), patch("services.live_safety.live_entry_blocked", return_value=(False, "ok")):
        out = await svc.submit_one(
            BrokerOrderRequest(symbol="AAPL", qty=1, side="buy", client_order_id=cid)
        )
    assert out.error == "kill_switch_entries_blocked"
    svc._live_buy_central_gates.assert_awaited()
    inner.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_submit_one_other_422_is_not_duplicate():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = "rid"
    inner.submit_order = AsyncMock(side_effect=_http_err(422, code=40010000, message="invalid qty"))
    inner.get_order_by_client_order_id = AsyncMock(side_effect=AssertionError("must not lookup"))
    svc = AlpacaOrderService(broker=inner)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out = await svc.submit_one(BrokerOrderRequest(symbol="SNAP", qty=1, side="buy"))
    assert out.status == "failed"
    assert "alpaca_422" in (out.error or "")
    inner.get_order_by_client_order_id.assert_not_called()


@pytest.mark.asyncio
async def test_held_bracket_stop_does_not_submit_new_stop():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = True
    inner.replace_order = AsyncMock(return_value={"id": "leg-1", "status": "held", "type": "stop"})
    inner.submit_order = AsyncMock(side_effect=AssertionError("must not submit new stop"))
    svc = AlpacaOrderService(broker=inner)
    held = BrokerOrderResult(
        id="leg-1",
        symbol="SNAP",
        qty=1,
        side="sell",
        type="stop",
        status="held",
        raw={"stop_price": "5.48"},
    )
    with patch.object(svc, "list_orders", AsyncMock(return_value=[held])) as listed, patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ), patch("services.live_safety.production_trading_unconfigured", return_value=False):
        out = await svc.replace_protective_stop(symbol="SNAP", qty=1, stop_price=5.60)
    listed.assert_awaited()
    assert listed.await_args.kwargs.get("status") == "all" or listed.await_args.args[0] == "all"
    assert out.id == "leg-1"
    inner.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_insufficient_qty_on_stop_does_not_close_or_unprotect():
    from services.alpaca_order_service import AlpacaOrderService
    from services.position_lifecycle_service import PositionLifecycleService
    from domain.ops import PositionMandate

    inner = MagicMock()
    inner.paper = True
    inner.submit_order = AsyncMock(side_effect=RuntimeError("insufficient qty available"))
    inner.get_order_by_client_order_id = AsyncMock(return_value=None)
    inner.get_positions = AsyncMock(return_value=[{"symbol": "SNAP", "qty": "1"}])
    svc = AlpacaOrderService(broker=inner)
    svc.get_positions = AsyncMock(return_value=[SimpleNamespace(symbol="SNAP", qty=1)])
    held = BrokerOrderResult(
        id="held-1",
        symbol="SNAP",
        qty=1,
        side="sell",
        type="stop",
        status="held",
        raw={"stop_price": "5.48"},
    )
    with patch.object(svc, "find_working_stop", AsyncMock(side_effect=[None, held])), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ), patch("services.live_safety.production_trading_unconfigured", return_value=False):
        out = await svc.replace_protective_stop(symbol="SNAP", qty=1, stop_price=5.60)
    assert out.id == "held-1"
    assert out.error is None
    assert out.raw.get("rechecked_after_insufficient_qty") is True
    inner.close_position = AsyncMock(side_effect=AssertionError("must not flatten"))

    life = PositionLifecycleService(MagicMock(), svc)
    life._settings = SimpleNamespace(lifecycle_sync_broker_stops=True)
    mandate = PositionMandate(symbol="SNAP", qty=1, entry_price=6, stop_loss=5.48)
    svc.replace_protective_stop = AsyncMock(
        return_value=BrokerOrderResult(
            symbol="SNAP", qty=1, side="sell", type="stop", status="failed",
            error="insufficient qty available",
        )
    )
    svc.find_working_stop = AsyncMock(return_value=held)
    svc.close_position = AsyncMock(side_effect=AssertionError("must not close"))
    detail = await life._sync_broker_stop(mandate, 5.48)
    assert "held intact" in (detail or "")
    assert "unprotected" not in (detail or "").lower()
    svc.close_position.assert_not_called()


def test_insufficient_qty_helper():
    assert is_insufficient_qty_error("insufficient qty available") is True
    assert is_insufficient_qty_error("asset not found") is False
    coded = _http_err(422, code=ALPACA_INSUFFICIENT_QTY_CODE, message="qty")
    assert is_insufficient_qty_error(coded) is True


def test_manual_source_tag_uuid_not_fixed_minus_one():
    from services.alpaca_order_service import AlpacaOrderService

    svc = AlpacaOrderService(broker=MagicMock())
    a = svc._build_order_payload(
        BrokerOrderRequest(symbol="AAPL", qty=1, side="sell", source_tag="voice")
    )
    b = svc._build_order_payload(
        BrokerOrderRequest(symbol="AAPL", qty=1, side="sell", source_tag="voice")
    )
    assert a["client_order_id"] != b["client_order_id"]
    assert a["client_order_id"].startswith("voice-")
    auto = svc._build_order_payload(
        BrokerOrderRequest(symbol="AAPL", qty=1, side="buy", source_tag="autopilot")
    )
    assert auto["client_order_id"].startswith("live-AAPL-")
    assert auto["client_order_id"].endswith("-1")


@pytest.mark.asyncio
async def test_reconcile_rejects_dead_or_mismatch():
    from services.alpaca_order_service import AlpacaOrderService

    broker = MagicMock()
    broker.last_request_id = None
    svc = AlpacaOrderService(broker=broker)
    req = BrokerOrderRequest(symbol="SNAP", qty=1, side="buy")
    dead = svc._reconcile_existing_order(
        req, {"id": "x", "symbol": "SNAP", "qty": "1", "side": "buy", "status": "canceled"}
    )
    assert dead.error == "stale_order:canceled"
    mismatch = svc._reconcile_existing_order(
        req, {"id": "x", "symbol": "AAPL", "qty": "1", "side": "buy", "status": "accepted"}
    )
    assert mismatch.error == "reconcile_symbol_mismatch"
    qty = svc._reconcile_existing_order(
        req, {"id": "x", "symbol": "SNAP", "qty": "3", "side": "buy", "status": "accepted"}
    )
    assert qty.error == "reconcile_qty_mismatch"
    ok = svc._reconcile_existing_order(
        req, {"id": "x", "symbol": "SNAP", "qty": "1", "side": "buy", "status": "accepted"}
    )
    assert ok.error is None
    assert ok.raw.get("reconciled") is True
    stop_req = BrokerOrderRequest(symbol="SNAP", qty=1, side="sell", order_type="stop", stop_price=5.4)
    for dead in ("filled", "replaced", "done_for_day", "canceled"):
        bad = svc._reconcile_existing_order(
            stop_req,
            {
                "id": "s",
                "symbol": "SNAP",
                "qty": "1",
                "side": "sell",
                "type": "stop",
                "status": dead,
                "stop_price": "5.4",
            },
        )
        assert bad.error == f"stale_order:{dead}"
        assert bad.raw.get("not_live_stop") is True
    live_stop = svc._reconcile_existing_order(
        stop_req,
        {
            "id": "s",
            "symbol": "SNAP",
            "qty": "1",
            "side": "sell",
            "type": "stop",
            "status": "held",
            "stop_price": "5.4",
        },
    )
    assert live_stop.error is None
    assert order_is_live_stop(live_stop) is True


@pytest.mark.asyncio
async def test_timeout_lookup_5xx_does_not_repost():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = "rid"
    inner.submit_order = AsyncMock(side_effect=httpx.ReadTimeout("no reply"))
    inner.get_order_by_client_order_id = AsyncMock(side_effect=_http_err(503, message="unavailable"))
    svc = AlpacaOrderService(broker=inner)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out = await svc.submit_one(
            BrokerOrderRequest(symbol="SNAP", qty=1, side="buy", client_order_id="live-SNAP-20261002-buy-1")
        )
    assert out.error == "order_uncertain_no_retry"
    inner.submit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_attempt_fail_closed_never_defaults_one():
    from services.order_idempotency import AttemptUnavailable

    with pytest.raises(AttemptUnavailable):
        await read_attempt(None, "SNAP:buy:20261002")
    flags = MagicMock()
    flags.get_json = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(AttemptUnavailable):
        await read_attempt(flags, "SNAP:buy:20261002")


@pytest.mark.asyncio
async def test_atomic_attempt_bump_returning():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from database.models import Base
    from database.repositories.ops_repository import OpsFlagRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        flags = OpsFlagRepository(session)
        n = await bump_attempt(flags, "SNAP:buy:20261002")
        assert n == 2
        n2 = await bump_attempt(flags, "SNAP:buy:20261002")
        assert n2 == 3
        assert await read_attempt(flags, "SNAP:buy:20261002") == 3
    await engine.dispose()
