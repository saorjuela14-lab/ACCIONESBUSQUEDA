"""Client-side stop watch + crypto fallback (paper only)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from services.protection import stop_watch as sw
from services.protection.stop_watch import (
    NotPaperError,
    ProtectedPosition,
    StopWatchService,
    next_ladder,
    watchdog_check,
)


class FakeBroker:
    base_url = "https://paper-api.alpaca.markets"

    def __init__(self, *, paper=True, fail_stop_limit=False, reject_oto=False):
        self.paper_flag = paper
        self.orders: list[dict] = []
        self.positions: dict[str, dict] = {}
        self.fail_stop_limit = fail_stop_limit
        self.reject_oto = reject_oto
        self.replaced: list[tuple] = []
        self.canceled: list[str] = []
        self._n = 0

    def is_configured(self):
        return True

    async def get_account(self):
        return {"paper": self.paper_flag, "account_number": "PA123" if self.paper_flag else "123"}

    async def submit_order(self, o):
        self._n += 1
        o = dict(o)
        if o.get("order_class") == "oto" and self.reject_oto:
            raise RuntimeError("oto not supported")
        if o.get("type") == "stop_limit" and self.fail_stop_limit:
            raise RuntimeError("stop_limit rejected")
        o.update(id=f"o{self._n}", status="new")
        if o["type"] == "market":
            o["status"] = "filled"
            q = float(o.get("qty") or 0) or round(float(o.get("notional", 0)) / 100.0, 6)
            o["filled_qty"], o["filled_avg_price"] = str(q), "100"
            key = o["symbol"].replace("/", "")
            if o["side"] == "buy":
                self.positions[key] = {"symbol": key, "qty": str(q), "avg_entry_price": "100",
                                       "asset_class": "crypto" if "/" in o["symbol"] else "us_equity"}
            else:
                self.positions.pop(key, None)
        self.orders.append(o)
        return o

    async def get_order_by_client_order_id(self, cid):
        return next((o for o in self.orders if o.get("client_order_id") == cid), None)

    async def replace_order(self, oid, **kw):
        self.replaced.append((oid, kw))
        return {"id": oid + "r", "status": "new"}

    async def cancel_order(self, oid):
        self.canceled.append(oid)
        for o in self.orders:
            if o["id"] == oid:
                o["status"] = "canceled"
        return {}

    async def get_positions(self):
        return list(self.positions.values())

    async def list_orders(self, status="open", limit=200):
        if status == "closed":
            return [o for o in self.orders if o["status"] in {"filled", "canceled"}]
        if status == "all":
            return [o for o in self.orders if o["status"] not in {"expired"}]
        return [o for o in self.orders if o["status"] in {"new", "accepted", "held", "pending_new", "partially_filled"}]


def _svc(tmp_path, broker, prices, **kw):
    async def q(s):
        return prices[s]
    alerts = []
    svc = StopWatchService(broker, q, alert=lambda k, d: alerts.append((k, d)),
                           state_path=tmp_path / "st.json", heartbeat_path=tmp_path / "hb.json", **kw)
    return svc, alerts


def _pos(sym="BTC/USD", ac="crypto", qty=0.5):
    return ProtectedPosition.from_entry(sym, ac, qty, 100.0, 95.0)  # R=5 -> TP 105/110/115


def _hold(broker, sym, qty):
    k = sym.replace("/", "")
    broker.positions[k] = {"symbol": k, "qty": str(qty), "avg_entry_price": "100",
                           "asset_class": "crypto" if "/" in sym else "us_equity"}


@pytest.fixture(autouse=True)
def _paper_env(monkeypatch):
    monkeypatch.delenv("CLIENT_STOP_WATCH_ENABLED", raising=False)
    monkeypatch.delenv("CLIENT_STOP_WATCH_LIVE_ACK", raising=False)


# 1
def test_crypto_buy_never_unprotected(monkeypatch):
    from services.multiasset import desk_service as ds
    b = FakeBroker()
    svc = ds.MultiAssetDeskService.__new__(ds.MultiAssetDeskService)
    svc._broker = b
    res = asyncio.run(svc._protect_after_fill(
        {"client_order_id": "c1", "status": "filled", "filled_qty": "0.5", "filled_avg_price": "100"},
        "BTC/USD", True, 0.05))
    stops = [o for o in b.orders if o["type"] == "stop_limit"]
    assert res["ok"] and len(stops) == 1
    assert stops[0]["time_in_force"] == "gtc" and float(stops[0]["stop_price"]) == 95.0
    assert float(stops[0]["limit_price"]) < 95.0


# 2
def test_crypto_stop_limit_failure_closes_position_and_alerts(tmp_path):
    b = FakeBroker(fail_stop_limit=True)
    _hold(b, "BTC/USD", 0.5)
    svc, alerts = _svc(tmp_path, b, {})
    pos = asyncio.run(svc.register(_pos()))
    assert pos.closed
    assert b._n == 4  # 3 protected-stop attempts, then market fail-safe close
    assert any(o["type"] == "market" and o["side"] == "sell" for o in b.orders)
    assert alerts and alerts[0][0] == "backup_stop_failed_closing"


# 3
def test_equity_oto_rejected_no_plain_retry_without_stop(monkeypatch):
    from services.multiasset import desk_service as ds
    src = open(ds.__file__).read()
    assert "oto_failed_retry_plain" not in src
    b = FakeBroker()
    svc = ds.MultiAssetDeskService.__new__(ds.MultiAssetDeskService)
    svc._broker = b
    asyncio.run(b.submit_order({"symbol": "GLD", "side": "buy", "type": "market", "qty": "2",
                                "time_in_force": "gtc", "client_order_id": "e1"}))
    res = asyncio.run(svc._protect_after_fill(b.orders[0], "GLD", False, 0.03))
    assert res["ok"] and any(o["type"] == "stop" and o["side"] == "sell" for o in b.orders)


# 4
def test_tp1_moves_stop_to_breakeven():
    p = _pos()
    assert next_ladder(p, 105.5) == ("raise", 100.0) and p.stage == 1


# 5
def test_tp2_moves_stop_to_tp1():
    p = _pos()
    next_ladder(p, 105.5); p.stop = 100.0
    assert next_ladder(p, 110.2) == ("raise", 105.0) and p.stage == 2


# 6
def test_tp3_closes_and_stop_hit_closes(tmp_path):
    b = FakeBroker(); _hold(b, "BTC/USD", 0.5)
    prices = {"BTC/USD": 116.0}
    svc, _ = _svc(tmp_path, b, prices)
    asyncio.run(svc.register(_pos()))
    asyncio.run(svc.tick())
    assert svc.positions["BTC/USD"].closed
    # stop hit: backup canceled before market sell
    b2 = FakeBroker(); _hold(b2, "AAPL", 1)
    svc2, _ = _svc(tmp_path / "b", b2, {"AAPL": 94.0})
    asyncio.run(svc2.register(_pos("AAPL", "equity", 1)))
    stop_id = svc2.positions["AAPL"].broker_stop_order_id
    asyncio.run(svc2.tick())
    assert svc2.positions["AAPL"].closed and stop_id in b2.canceled


# 7
def test_stop_never_moves_down(tmp_path):
    b = FakeBroker(); _hold(b, "BTC/USD", 0.5)
    prices = {"BTC/USD": 106.0}
    svc, _ = _svc(tmp_path, b, prices)
    asyncio.run(svc.register(_pos()))
    asyncio.run(svc.tick())
    assert svc.positions["BTC/USD"].stop == 100.0
    prices["BTC/USD"] = 101.0
    asyncio.run(svc.tick())
    assert svc.positions["BTC/USD"].stop == 100.0 and not svc.positions["BTC/USD"].closed


# 8
def test_backup_stop_synced_after_raise_and_partial(tmp_path):
    b = FakeBroker(); _hold(b, "BTC/USD", 0.5)
    prices = {"BTC/USD": 106.0}
    svc, _ = _svc(tmp_path, b, prices)
    asyncio.run(svc.register(_pos()))
    first = svc.positions["BTC/USD"].broker_stop_order_id
    asyncio.run(svc.tick())
    live = [o for o in b.orders if o["type"] == "stop_limit" and o["status"] == "new"]
    assert first in b.canceled and len(live) == 1 and float(live[0]["stop_price"]) == 100.0
    _hold(b, "BTC/USD", 0.3)  # partial sold elsewhere
    asyncio.run(svc.tick())
    live = [o for o in b.orders if o["type"] == "stop_limit" and o["status"] == "new"]
    assert len(live) == 1 and float(live[0]["qty"]) == 0.3


# 9
def test_orphan_position_gets_default_stop_and_alert(tmp_path):
    b = FakeBroker(); _hold(b, "ETH/USD", 1.0)
    svc, alerts = _svc(tmp_path, b, {"ETH/USD": 100.0}, default_stop_pct=0.03)
    asyncio.run(svc.tick())
    p = svc.positions["ETH/USD"]
    assert p.stop == 97.0 and p.broker_stop_order_id
    assert any(k == "orphan_position" for k, _ in alerts)


# 10
def test_heartbeat_stale_triggers_alert_and_verifies_broker_stops(tmp_path):
    b = FakeBroker(); _hold(b, "BTC/USD", 0.5)
    svc, alerts = _svc(tmp_path, b, {"BTC/USD": 100.0})
    asyncio.run(svc.register(_pos()))
    asyncio.run(b.cancel_order(svc.positions["BTC/USD"].broker_stop_order_id))  # stop vanished
    svc.heartbeat_path.write_text(json.dumps({"ts": time.time() - 999}))
    out = asyncio.run(watchdog_check(svc, max_age_s=90))
    assert out["stale"] and "BTC/USD" in out["verified"]
    assert any(k == "stop_watch_heartbeat_stale" for k, _ in alerts)
    assert len([o for o in b.orders if o["type"] == "stop_limit" and o["status"] == "new"]) == 1


# 11
def test_flag_default_off_in_live_and_guard(tmp_path, monkeypatch):
    assert sw.stop_watch_enabled(paper=True) is True
    assert sw.stop_watch_enabled(paper=False) is False
    monkeypatch.setenv("CLIENT_STOP_WATCH_ENABLED", "true")
    assert sw.stop_watch_enabled(paper=False) is False  # needs explicit LIVE ack
    b = FakeBroker(paper=False)
    svc, _ = _svc(tmp_path, b, {})
    with pytest.raises(NotPaperError):
        asyncio.run(svc.tick())
    b2 = FakeBroker(); b2.base_url = "https://api.alpaca.markets"
    svc2, _ = _svc(tmp_path / "x", b2, {})
    with pytest.raises(NotPaperError):
        asyncio.run(svc2.register(_pos()))
    assert b.orders == [] and b2.orders == []


# 12
def test_idempotent_ticks_do_not_duplicate_orders(tmp_path):
    b = FakeBroker(); _hold(b, "AAPL", 2)
    svc, _ = _svc(tmp_path, b, {"AAPL": 101.0})
    asyncio.run(svc.register(_pos("AAPL", "equity", 2)))
    n = len(b.orders)
    for _ in range(3):
        asyncio.run(svc.tick())
    assert len(b.orders) == n and not b.replaced
    # state persisted and reloaded
    svc2, _ = _svc(tmp_path, b, {"AAPL": 101.0})
    assert svc2.positions["AAPL"].broker_stop_order_id == svc.positions["AAPL"].broker_stop_order_id


def test_missing_broker_stop_is_replaced_on_tick(tmp_path):
    b = FakeBroker(); _hold(b, "ETH/USD", 1.0)
    svc, alerts = _svc(tmp_path, b, {"ETH/USD": 100.0})
    asyncio.run(svc.register(_pos("ETH/USD")))
    asyncio.run(b.cancel_order(svc.positions["ETH/USD"].broker_stop_order_id))
    asyncio.run(svc.tick())
    assert any(k == "backup_stop_missing" for k, _ in alerts)
    assert len([o for o in b.orders if o["type"] == "stop_limit" and o["status"] == "new"]) == 1


# 14
class StrictBroker(FakeBroker):
    """Sells with nothing to sell fail like Alpaca: 403 insufficient balance."""

    async def submit_order(self, o):
        key = o["symbol"].replace("/", "")
        if o.get("side") == "sell" and float((self.positions.get(key) or {}).get("qty") or 0) <= 0:
            self.sell_attempts = getattr(self, "sell_attempts", 0) + 1
            raise RuntimeError('403 {"code":40310000,"message":"insufficient balance for BTC (requested: 0.5, available: 0)"}')
        return await super().submit_order(o)

    async def list_orders(self, status="open", limit=200):
        if status == "closed":
            return [o for o in self.orders if o["status"] in {"filled", "canceled"}]
        return await super().list_orders(status, limit)


def test_position_closed_at_broker_while_down_is_marked_gone_without_orders(tmp_path):
    b = StrictBroker(); _hold(b, "BTC/USD", 0.5)
    svc, _ = _svc(tmp_path, b, {"BTC/USD": 94.0})
    asyncio.run(svc.register(_pos()))
    stop_id = svc.positions["BTC/USD"].broker_stop_order_id
    # watcher down: the broker stop_limit fills and the position disappears
    so = next(o for o in b.orders if o["id"] == stop_id)
    so.update(status="filled", filled_avg_price="94.9", filled_qty="0.5")
    b.positions.clear()
    svc.heartbeat_path.write_text(json.dumps({"ts": time.time() - 999}))
    n = len(b.orders)

    # restart: startup reconciliation (watchdog) + ticks
    svc2, alerts = _svc(tmp_path, b, {"BTC/USD": 94.0})
    out = asyncio.run(watchdog_check(svc2, max_age_s=90))
    p = svc2.positions["BTC/USD"]
    assert out["verified"] == [] and p.closed
    gone = [h for h in p.history if h["event"] == "gone_at_broker"]
    assert gone and gone[-1]["fill_price"] == 94.9 and gone[-1]["fill_order_id"] == stop_id
    for _ in range(3):
        asyncio.run(svc2.tick())
    assert len(b.orders) == n and getattr(b, "sell_attempts", 0) == 0
    assert not any(k == "backup_stop_failed_closing" for k, _ in alerts)

    # tick path directly (no watchdog): stop hit with position gone -> no market sell
    b2 = StrictBroker(); _hold(b2, "AAPL", 1)
    svc3, _ = _svc(tmp_path / "t", b2, {"AAPL": 94.0})
    asyncio.run(svc3.register(_pos("AAPL", "equity", 1)))
    b2.positions["AAPL"]["qty"] = "0"  # broker reports qty 0
    n2 = len(b2.orders)
    asyncio.run(svc3.tick())
    assert svc3.positions["AAPL"].closed and len(b2.orders) == n2
    assert getattr(b2, "sell_attempts", 0) == 0

    # 403 insufficient balance on a sell -> re-check position, no retry loop
    b3 = StrictBroker(); _hold(b3, "ETH/USD", 1.0)
    svc4, _ = _svc(tmp_path / "u", b3, {"ETH/USD": 100.0})
    pos = _pos("ETH/USD", qty=1.0)
    svc4.positions[pos.symbol] = pos

    real_place = svc4._place_backup

    async def place(p):
        b3.positions.clear()
        return await real_place(p)
    svc4._place_backup = place
    assert asyncio.run(svc4.ensure_backup(pos)) is False
    assert pos.closed and b3.sell_attempts == 1  # one failed attempt, then reconciled


class SlowPositionsBroker(FakeBroker):
    async def get_positions(self):
        await asyncio.sleep(10)
        return []


def test_broker_call_timeout_fails_closed_without_hanging(tmp_path):
    b = SlowPositionsBroker()
    svc, _ = _svc(tmp_path, b, {}, broker_timeout_s=0.01)
    qty = asyncio.run(svc._broker_qty(_pos()))
    assert qty is None


def test_startup_reconciliation_adopts_live_stop_before_any_replace(tmp_path):
    b = FakeBroker()
    _hold(b, "AAPL", 1)
    b.orders.append(
        {
            "id": "child-stop",
            "symbol": "AAPL",
            "side": "sell",
            "type": "stop",
            "status": "held",
            "stop_price": "97",
            "qty": "1",
        }
    )
    svc, _ = _svc(tmp_path, b, {"AAPL": 100.0}, file_backend=False)
    pos = _pos("AAPL", "equity", 1)
    pos.broker_stop_order_id = "stale-stop"
    pos.broker_stop_price = 95.0
    svc.positions["AAPL"] = pos

    out = asyncio.run(watchdog_check(svc, max_age_s=90))

    assert out["stale"] is True and out["verified"] == ["AAPL"]
    assert pos.broker_stop_order_id == "child-stop"
    assert pos.stop == 97.0
    assert not b.replaced and not b.canceled
    assert len(b.orders) == 1
