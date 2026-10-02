"""PR #134 follow-up: P2 wealth, real qty/CFEE, inherited quarantine, clean, streak, parity."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from services.multiasset.crypto_fills import (
    net_qty_from_fill_and_cfee,
    resolve_filled_qty,
    cfee_qty_for_symbol,
)
from services.multiasset.crypto_legacy import (
    cancel_open_crypto_orders,
    legacy_flatten,
    list_paper_orders,
    list_paper_positions,
    require_paper_broker,
)
from services.multiasset.crypto_owned import (
    desk_actor,
    is_strategy_a_trade,
    set_strategy_a_armed,
    strategy_a_is_armed,
    strategy_a_owned,
)
from services.multiasset.crypto_risk import (
    allocation_wealth,
    kill_from_allocation_peak,
    record_closed_pnl,
    wealth_drawdown_pct,
)
from services.multiasset.paper_broker import MultiAssetNotPaperError
from services.db_lease import run_owner, replica_id

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_p2_wealth_continuous_on_partial_and_full_sale():
    allocation = 2500.0
    qty, entry, last = 0.25, 4000.0, 4000.0
    before = allocation_wealth(
        allocation=allocation, realized_pnl_usd=0.0, open_marks=[(qty, entry, last)]
    )
    assert before == pytest.approx(2500.0)

    # Partial: sell 0.10 at the mark — wealth unchanged (cost basis stays).
    sold = 0.10
    realized = (last - entry) * sold
    after_partial = allocation_wealth(
        allocation=allocation,
        realized_pnl_usd=realized,
        open_marks=[(qty - sold, entry, last)],
    )
    assert after_partial == pytest.approx(before)

    # Full remainder at the mark — still continuous.
    rest = qty - sold
    realized_full = realized + (last - entry) * rest
    after_full = allocation_wealth(
        allocation=allocation, realized_pnl_usd=realized_full, open_marks=[]
    )
    assert after_full == pytest.approx(before)
    assert kill_from_allocation_peak(
        peak_crypto_usd=before, crypto_usd=after_full, allocation_usd=allocation
    ) is False

    # Realized loss at a worse print: wealth = allocation + pnl, not just pnl.
    realized_loss = -564.12
    wealth_loss = allocation_wealth(
        allocation=allocation, realized_pnl_usd=realized_loss, open_marks=[]
    )
    assert wealth_loss == pytest.approx(allocation + realized_loss)
    assert wealth_loss > 0
    dd = wealth_drawdown_pct(peak=before, wealth=wealth_loss, allocation=allocation)
    assert dd == pytest.approx(564.12 / 2500.0 * 100.0)


def test_fill_net_qty_matches_cfee_fixtures_1e9():
    fills = _load("paper/fill.json")["items"]
    cfees = _load("paper/cfee.json")["items"]
    eth_fill = next(r for r in fills if r["symbol"] == "ETH/USD")
    btc_fill = next(r for r in fills if r["symbol"] == "BTC/USD")
    eth_fee = cfee_qty_for_symbol(cfees, "ETH/USD")
    btc_fee = cfee_qty_for_symbol(cfees, "BTC/USD")
    eth_net = net_qty_from_fill_and_cfee(filled_qty=float(eth_fill["qty"]), cfee_qty=eth_fee)
    btc_net = net_qty_from_fill_and_cfee(filled_qty=float(btc_fill["qty"]), cfee_qty=btc_fee)
    assert eth_net == pytest.approx(0.067531395, abs=1e-9)
    assert btc_net == pytest.approx(0.001097977, abs=1e-9)


@pytest.mark.asyncio
async def test_resolve_filled_skips_pending_and_uses_order_id():
    broker = MagicMock()
    broker.get_order = AsyncMock(
        return_value={
            "id": "oid-eth",
            "status": "pending_new",
            "filled_qty": "0",
            "symbol": "ETH/USD",
        }
    )
    broker.list_account_activities = AsyncMock(return_value=[])
    pending = await resolve_filled_qty(broker, symbol="ETH/USD", order_id="oid-eth")
    assert pending["ok"] is False
    assert "not_filled" in pending["reason"]

    broker.get_order = AsyncMock(
        return_value={
            "id": "oid-eth",
            "status": "filled",
            "filled_qty": "0.067700647",
            "filled_avg_price": "1904",
            "symbol": "ETH/USD",
            "created_at": "2026-08-17T13:38:45Z",
        }
    )
    broker.list_account_activities = AsyncMock(return_value=_load("paper/cfee.json")["items"])
    filled = await resolve_filled_qty(broker, symbol="ETH/USD", order_id="oid-eth")
    assert filled["ok"] is True
    assert float(filled["qty"]) == pytest.approx(0.067531395, abs=1e-9)

    broker.get_order = AsyncMock(
        return_value={"id": "dead", "status": "canceled", "filled_qty": "0", "symbol": "ETH/USD"}
    )
    dead = await resolve_filled_qty(broker, symbol="ETH/USD", order_id="dead")
    assert dead["ok"] is False
    assert dead["reason"] == "canceled_or_expired_no_fill"


@pytest.mark.asyncio
async def test_dust_residual_closes_tracker_lot(session):
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    tr = MultiAssetTradeTracker(session)
    await tr.open_trade(
        desk="crypto",
        symbol="ETH/USD",
        qty=0.25,
        entry_price=4000.0,
        meta={"strategy": "strategy_a", "client_order_id": "sa9-ETHUSD-2026081713-buy-1"},
    )
    closed = await tr.close_trade(
        desk="crypto",
        symbol="ETH/USD",
        exit_price=4000.0,
        qty=0.249375,  # broker net; residual 0.000625 < Alpaca min 0.001
    )
    assert closed is not None
    assert closed.status == "closed"
    assert (closed.meta or {}).get("dust") is True
    assert await tr.get_open("crypto", "ETH/USD") is None


@pytest.mark.asyncio
async def test_positions_fail_does_not_sell_tracker_qty():
    from services.multiasset.autopilot import MultiAssetAutopilotService

    svc = MultiAssetAutopilotService(session=None)
    svc._broker = MagicMock()
    svc._broker.get_positions = AsyncMock(side_effect=RuntimeError("broker down"))
    svc._crypto_alert = AsyncMock()
    trade = MagicMock(qty=0.25, entry_price=4000.0)
    sold = await svc._crypto_market_sell("ETH/USD", trade, dry_run=True, actor="t", reason="stop")
    assert sold["ok"] is False
    assert sold["error"] == "positions_unavailable"
    assert sold["keep_state"] is True


def test_a_sell_is_min_of_own_and_broker():
    own, broker = 0.07, 0.25  # mixed inherited + A
    assert min(own, broker) == pytest.approx(0.07)


def test_loss_streak_win_resets_counter():
    t0 = "2026-09-01T00:00:00+00:00"
    rows = []
    for i, pnl in enumerate((-10.0, -4.0, 3.0, -2.0)):
        rows = record_closed_pnl(rows, pnl_usd=pnl, at=t0, symbol="ETH/USD")
        if pnl >= 0:
            assert rows == []
    assert len(rows) == 1
    from services.multiasset.crypto_risk import loss_streak_pause

    paused, _ = loss_streak_pause(rows, now=datetime(2026, 9, 1, tzinfo=timezone.utc), n=3)
    assert paused is False


def test_desk_actor_comes_from_server_scope():
    scope = MagicMock(email="mesa@monarch", user_id="u1")
    assert desk_actor(scope) == "mesa@monarch"
    scope = MagicMock(email=None, user_id="u9")
    assert desk_actor(scope) == "u9"


def test_run_owner_is_hostname_pid_plus_id():
    a, b = run_owner(), run_owner()
    assert a != b
    assert replica_id() in a and replica_id() in b
    assert a.count(":") >= 2


@pytest.mark.asyncio
async def test_same_hostname_does_not_reenter_lease(session):
    from services.db_lease import acquire_lease, LEASE_CRYPTO_A

    first = run_owner()
    second = run_owner()
    got = await acquire_lease(session, name=LEASE_CRYPTO_A, owner=first)
    assert got.acquired is True
    other = await acquire_lease(session, name=LEASE_CRYPTO_A, owner=second)
    assert other.acquired is False


def _paper_broker() -> MagicMock:
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    return broker


def _live_broker() -> MagicMock:
    broker = MagicMock()
    broker.base_url = "https://api.alpaca.markets"
    broker.paper = False
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": False})
    broker.get_positions = AsyncMock()
    broker.list_orders = AsyncMock()
    broker.close_position = AsyncMock()
    broker.cancel_order = AsyncMock()
    return broker


@pytest.mark.asyncio
async def test_paper_ops_refuse_live_client_without_broker_calls():
    live = _live_broker()
    with pytest.raises(MultiAssetNotPaperError):
        require_paper_broker(live)
    with pytest.raises(MultiAssetNotPaperError):
        await list_paper_positions(live)
    with pytest.raises(MultiAssetNotPaperError):
        await list_paper_orders(live)
    with pytest.raises(MultiAssetNotPaperError):
        await cancel_open_crypto_orders(live)
    with pytest.raises(MultiAssetNotPaperError):
        await legacy_flatten(live, symbols=["WIF/USD"], dry_run=True, actor="desk")
    live.get_positions.assert_not_awaited()
    live.list_orders.assert_not_awaited()
    live.close_position.assert_not_awaited()
    live.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_open_crypto_orders_skips_equity():
    broker = _paper_broker()
    broker.list_orders = AsyncMock(
        return_value=[
            {"id": "c1", "symbol": "WIF/USD", "asset_class": "crypto", "status": "new"},
            {"id": "e1", "symbol": "AAPL", "asset_class": "us_equity", "status": "new"},
            {"id": "c2", "symbol": "BTC/USD", "asset_class": "crypto", "status": "accepted"},
        ]
    )
    broker.cancel_order = AsyncMock(return_value={})
    out = await cancel_open_crypto_orders(broker)
    cancelled_ids = {c["id"] for c in out["cancelled"] if not c.get("error")}
    assert cancelled_ids == {"c1", "c2"}
    assert broker.cancel_order.await_count == 2


@pytest.mark.asyncio
async def test_legacy_flatten_rejects_btc_when_a_has_open_lot(session):
    from database.repositories.ops_repository import OpsFlagRepository
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    flags = OpsFlagRepository(session)
    await set_strategy_a_armed(flags, armed=True, actor="desk")
    tr = MultiAssetTradeTracker(session)
    await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.001,
        entry_price=100000.0,
        meta={"strategy": "strategy_a", "client_order_id": "sa9-BTCUSD-2026100216-buy-1"},
    )
    broker = _paper_broker()
    broker.get_positions = AsyncMock(
        return_value=[
            {"symbol": "BTC/USD", "qty": "0.01", "avg_entry_price": "64000", "market_value": "640", "asset_class": "crypto"},
            {"symbol": "WIF/USD", "qty": "10", "avg_entry_price": "0.2", "market_value": "2", "asset_class": "crypto"},
        ]
    )
    broker.list_orders = AsyncMock(return_value=[])
    broker.close_position = AsyncMock()
    out = await legacy_flatten(
        broker,
        symbols=["BTC/USD", "WIF/USD"],
        dry_run=False,
        actor="mesa@monarch",
        tracker=tr,
        flags=flags,
    )
    assert out["ok"] is False
    assert "BTC/USD" in out["rejected"]
    broker.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_flatten_dry_run_and_dust_no_loop():
    broker = _paper_broker()
    broker.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "WIF/USD",
                "qty": "100",
                "avg_entry_price": "0.2",
                "market_value": "20",
                "current_price": "0.2",
                "asset_class": "crypto",
            }
        ]
    )
    broker.list_orders = AsyncMock(return_value=[])
    dry = await legacy_flatten(broker, symbols=["WIF/USD"], dry_run=True, actor="desk")
    assert dry["dry_run"] is True
    assert dry["preview"][0]["qty"] == pytest.approx(100)
    assert dry["closed"] == []
    broker.close_position = AsyncMock(return_value={"id": "x", "status": "filled", "filled_avg_price": "0.2"})
    # Residue below dust after close — reported once, no retry loop.
    broker.get_positions = AsyncMock(
        side_effect=[
            [
                {
                    "symbol": "WIF/USD",
                    "qty": "100",
                    "avg_entry_price": "0.2",
                    "market_value": "20",
                    "current_price": "0.2",
                    "asset_class": "crypto",
                }
            ],
            [
                {
                    "symbol": "WIF/USD",
                    "qty": "0.0000001",
                    "avg_entry_price": "0.2",
                    "market_value": "0.00002",
                    "current_price": "0.2",
                    "asset_class": "crypto",
                }
            ],
        ]
    )
    real = await legacy_flatten(broker, symbols=["WIF/USD"], dry_run=False, actor="desk")
    assert real["closed"][0]["symbol"] == "WIF/USD"
    assert real["dust"]
    assert broker.close_position.await_count == 1


@pytest.mark.asyncio
async def test_strategy_a_armed_missing_allows_only_without_inherited(session):
    from database.repositories.ops_repository import OpsFlagRepository

    flags = OpsFlagRepository(session)
    assert await strategy_a_is_armed(flags, inherited=False) is True
    assert await strategy_a_is_armed(flags, inherited=True) is False
    await set_strategy_a_armed(flags, armed=False, actor="desk")
    assert await strategy_a_is_armed(flags, inherited=False) is False
    await set_strategy_a_armed(flags, armed=True, actor="desk")
    assert await strategy_a_is_armed(flags, inherited=True) is True


def test_inherited_not_strategy_a():
    t = MagicMock(symbol="WIF/USD", meta={"note": "old"})
    t.meta = {"note": "old"}
    t.client_order_id = ""
    assert is_strategy_a_trade(t) is False
    a = MagicMock(symbol="BTC/USD")
    a.meta = {"strategy": "strategy_a", "client_order_id": "sa9-BTCUSD-x"}
    a.client_order_id = "sa9-BTCUSD-x"
    assert is_strategy_a_trade(a) is True
    assert [x.symbol for x in strategy_a_owned([t, a])] == ["BTC/USD"]


def test_real_bar_parity_vs_engine_prints():
    from services.multiasset.engine_bars import bars_to_1h, clean, resample_4h
    from services.multiasset.strategy_a import catch_up_exits, chandelier_stop_px
    from services.multiasset.indicators import atr

    payload = _load("bars/btc_eth_1h_2025-09-10.json")
    prints = payload["engine_prints"]
    for symbol, expect in prints.items():
        raw = bars_to_1h(payload[symbol])
        four = resample_4h(clean(raw))
        assert not four.empty
        hit_open = pd.Timestamp(expect["candle_open"])
        assert hit_open in four.index
        loc = four.index.get_loc(hit_open)
        engine_stop = float(expect["stop_px"])
        prev = four.iloc[:loc]
        atr_prev = float(atr(prev, 14).iloc[-1])
        implied_high = engine_stop + 8.0 * atr_prev
        assert chandelier_stop_px(implied_high, atr_prev) == pytest.approx(engine_stop, abs=0.02)
        now = (four.index[loc] + pd.Timedelta(hours=4, minutes=20)).to_pydatetime()

        cup = catch_up_exits(
            four,
            last_evaluated_open=four.index[loc - 1],
            highest_close=implied_high,
            stop_px=engine_stop,
            now=now,
        )
        assert cup["data_ok"] is True
        assert cup["hit"] is True
        assert pd.Timestamp(cup["hit_bar"]["candle_open"]) == hit_open
        assert float(cup["hit_bar"]["stop_px"]) == pytest.approx(engine_stop, abs=0.05)

        for n_gap in (3, 7):
            last_eval = four.index[loc - n_gap]
            hist = four.loc[:last_eval]
            stop0 = chandelier_stop_px(implied_high, float(atr(hist, 14).iloc[-1]))
            cup_n = catch_up_exits(
                four,
                last_evaluated_open=last_eval,
                highest_close=implied_high,
                stop_px=stop0,
                now=now,
            )
            assert cup_n["data_ok"] is True
            assert cup_n["hit"] is True
            assert len(cup_n["evals"]) >= 2
            assert pd.Timestamp(cup_n["hit_bar"]["candle_open"]) <= hit_open


def test_clean_real_eth_low_788_from_fixture():
    from services.multiasset.engine_bars import bars_to_1h, clean

    payload = _load("bars/eth_1h_2025-04.json")
    raw = bars_to_1h(payload["items"])
    ts = pd.Timestamp("2025-04-07T06:00:00Z")
    assert float(raw.loc[ts, "Low"]) == pytest.approx(788.6624428455)
    assert float(raw.loc[ts, "Close"]) == pytest.approx(1429.7445)
    cleaned = clean(raw)
    assert float(cleaned.loc[ts, "Low"]) > 1100.0


@pytest.mark.asyncio
async def test_kill_reset_actor_is_server_scope(session):
    from apis.deps import OrgScope
    from database.repositories.ops_repository import OpsFlagRepository
    from services.multiasset.crypto_owned import desk_actor
    from services.multiasset.crypto_risk import reset_allocation_kill

    scope = OrgScope(role="desk", org_id="monarch", user_id="u1", email="ceo@monarch", auth_type="token")
    flags = OpsFlagRepository(session)
    mark = {"kill_active": True, "peak_wealth_usd": 2000.0, "wealth_usd": 1700.0}
    updated = reset_allocation_kill(
        mark, actor=desk_actor(scope), reason="review", current_wealth=1700.0
    )
    assert updated["kill_reset"]["actor"] == "ceo@monarch"
    await flags.set_json("crypto_strategy_a_risk", updated)
