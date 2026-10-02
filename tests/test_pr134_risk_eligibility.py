"""PR #134 Risk B1–B6, eligibility A–D, last-cycle fill split, spread rejects."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base
from services.multiasset.crypto_filters import (
    EVIDENCE_GRADE_PAPER,
    LIVE_MIN_OOS_TRADES,
    PAPER_MIN_OOS_TRADES,
    count_live_eligibility_trades,
    eligibility_threshold,
    evidence_gate,
    is_live_eligibility_evidence,
    screen_symbol,
)
from services.multiasset.crypto_fills import (
    BUY_NET_QTY_RATE,
    buy_net_qty,
    position_qty_delta_alert,
)
from services.multiasset.crypto_obs import classify_cycle_fills, classify_cycle_side
from services.multiasset.crypto_risk import (
    entry_spread_ok,
    sizing_allocation,
    spread_reject_record,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _paper_broker() -> MagicMock:
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    return broker


def _live_broker() -> MagicMock:
    broker = MagicMock()
    broker.base_url = "https://api.alpaca.markets"
    broker.paper = False
    broker.is_configured.return_value = True
    return broker


def _btc_row(*, n_trades, expectancy=0.085, universe=True, status="oos"):
    return {
        "symbol": "BTC/USD",
        "expectancy": expectancy,
        "n_trades": n_trades,
        "on_approved_universe": universe,
        "status": status,
        "median_spread_bps": 8.0,
        "median_adv_usd": 2e10,
    }


def test_evidence_gate_always_requires_expectancy_and_n_trades():
    row = _btc_row(n_trades=18, universe=True, status="approved")
    ok_live, why_live = evidence_gate(row, min_trades=LIVE_MIN_OOS_TRADES)
    assert ok_live is False
    assert "18" in why_live
    ok_paper, _ = evidence_gate(row, min_trades=PAPER_MIN_OOS_TRADES)
    assert ok_paper is True

    null_exp = _btc_row(n_trades=40, expectancy=None, universe=True, status="approved")
    assert evidence_gate(null_exp, min_trades=10)[0] is False
    assert evidence_gate(null_exp, min_trades=20)[0] is False

    autopilot = {"symbol": "BTC/USD", "on_approved_universe": True, "n_trades": 5, "expectancy": 0.2}
    assert evidence_gate(autopilot, min_trades=10)[0] is False
    assert evidence_gate(autopilot, min_trades=20)[0] is False

    missing_n = {"symbol": "BTC/USD", "on_approved_universe": True, "expectancy": 0.2}
    assert evidence_gate(missing_n, min_trades=10) == (False, "n_trades_missing")

    restrict = _btc_row(n_trades=40, universe=False)
    assert evidence_gate(restrict, min_trades=10)[0] is False


def test_eligibility_threshold_paper_is_code_constant_not_env(monkeypatch):
    monkeypatch.setenv("CRYPTO_MIN_OOS_TRADES", "1")
    monkeypatch.setenv("CRYPTO_PAPER_MIN_OOS_TRADES", "1")
    paper = eligibility_threshold(_paper_broker())
    assert paper["mode"] == "paper"
    assert paper["min_trades"] == 10
    assert paper["min_trades"] == PAPER_MIN_OOS_TRADES
    live = eligibility_threshold(_live_broker())
    assert live["mode"] == "live"
    assert live["min_trades"] == 20
    doubt = eligibility_threshold(None)
    assert doubt["mode"] == "live" and doubt["min_trades"] == 20
    weird = MagicMock()
    weird.base_url = "https://paper-api.alpaca.markets"
    weird.paper = None
    assert eligibility_threshold(weird)["min_trades"] == 20


def test_paper_trades_are_not_live_eligibility_evidence():
    paper = MagicMock()
    paper.meta = {"strategy": "strategy_a", "evidence_grade": EVIDENCE_GRADE_PAPER}
    live = MagicMock()
    live.meta = {"strategy": "strategy_a", "evidence_grade": "oos"}
    assert is_live_eligibility_evidence(paper) is False
    assert is_live_eligibility_evidence(live) is True
    assert count_live_eligibility_trades([paper, live, paper]) == 1


def test_screen_n18_live_blocks_paper_passes():
    row = _btc_row(n_trades=18)
    live = screen_symbol(
        row, min_adv_usd=1, min_trades=20, live_spread_bps=4.0, adv_usd=2e10, tradable=True
    )
    paper = screen_symbol(
        row, min_adv_usd=1, min_trades=10, live_spread_bps=4.0, adv_usd=2e10, tradable=True
    )
    assert live["passed"] is False
    assert paper["passed"] is True


def test_spread_reject_is_recorded_never_silent():
    ok, why = entry_spread_ok("BTC/USD", live_bps=30.0, median_bps=8.0)
    assert ok is False
    rec = spread_reject_record("BTC/USD", live_bps=30.0, median_bps=8.0, reason=why)
    assert rec["symbol"] == "BTC/USD"
    assert rec["observed_spread_bps"] == 30.0
    assert rec["threshold_bps"] == pytest.approx(20.0)
    desk = {"buys": [], "sells": [], "spread_rejects": [rec]}
    counts = classify_cycle_fills(desk)
    assert counts["spread_reject_count"] == 1
    assert counts["spread_rejects"][0]["symbol"] == "BTC/USD"


def test_last_cycle_pending_new_is_not_a_fill():
    buys = [
        {"symbol": "BTC/USD", "order_id": "oid-pending", "status": "pending_new", "ok": True},
        {"symbol": "ETH/USD", "order_id": "oid-fill", "status": "filled", "tracked": True},
        {"symbol": "BTC/USD", "order_id": "oid-rej", "status": "rejected", "error": "422"},
    ]
    sells = [
        {"symbol": "ETH/USD", "order_id": "sid-pend", "status": "pending_new", "ok": True},
    ]
    out = classify_cycle_fills({"buys": buys, "sells": sells})
    assert out["buys_submitted"] == 3
    assert out["buys_filled"] == 1
    assert out["buys"] == 1
    assert [p["order_id"] for p in out["buys_pending"]] == ["oid-pending"]
    assert [p["order_id"] for p in out["buys_rejected"]] == ["oid-rej"]
    assert out["sells_submitted"] == 1
    assert out["sells_filled"] == 0
    assert out["sells"] == 0
    assert [p["order_id"] for p in out["sells_pending"]] == ["sid-pend"]
    pending_only = classify_cycle_side(
        [{"symbol": "BTC/USD", "order_id": "x", "status": "pending_new"}], side="buy"
    )
    assert pending_only["buys_filled"] == 0
    assert pending_only["buys"] == 0


def test_buy_net_qty_uses_rate_not_cfee_subtraction():
    assert BUY_NET_QTY_RATE == 0.9975
    assert buy_net_qty(1.0) == pytest.approx(0.9975)
    assert buy_net_qty(0.067700647) == pytest.approx(0.067531395, abs=1e-9)
    alert = position_qty_delta_alert(0.9975, 0.99)
    assert alert and alert["alert"] is True
    assert position_qty_delta_alert(0.9975, 0.9975) is None


def test_sizing_allocation_falls_never_above_fixed():
    fixed = 2500.0
    assert sizing_allocation(fixed_allocation=fixed, equity=8000.0) == pytest.approx(2000.0)
    assert sizing_allocation(fixed_allocation=fixed, equity=20_000.0) == pytest.approx(2500.0)
    assert sizing_allocation(fixed_allocation=fixed, equity=10_000.0) == pytest.approx(2500.0)


def test_clean_skips_clip_when_n_ge_200():
    from services.multiasset.engine_bars import clip_outlier_prints

    idx = pd.date_range("2025-04-01", periods=30, freq="h", tz="UTC")
    close = pd.Series([1500.0] * 30, index=idx)
    close.iloc[15] = 700.0
    df = pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close.copy(),
            "Close": close,
            "n": [250] * 30,
        },
        index=idx,
    )
    df.loc[idx[15], "Low"] = 700.0
    out = clip_outlier_prints(df)
    assert float(out.loc[idx[15], "Low"]) == pytest.approx(700.0)


@pytest.mark.asyncio
async def test_flatten_rejects_a_lot_even_when_disarmed(session):
    from database.repositories.ops_repository import OpsFlagRepository
    from services.multiasset.crypto_legacy import legacy_flatten
    from services.multiasset.crypto_owned import set_strategy_a_armed
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    flags = OpsFlagRepository(session)
    await set_strategy_a_armed(flags, armed=False, actor="desk")
    tr = MultiAssetTradeTracker(session)
    await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.001,
        entry_price=100000.0,
        meta={"strategy": "strategy_a", "client_order_id": "sa9-BTCUSD-x"},
    )
    broker = _paper_broker()
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.get_positions = AsyncMock(
        return_value=[
            {"symbol": "BTC/USD", "qty": "0.01", "avg_entry_price": "64000", "asset_class": "crypto"},
            {"symbol": "WIF/USD", "qty": "10", "avg_entry_price": "0.2", "asset_class": "crypto"},
        ]
    )
    broker.list_orders = AsyncMock(return_value=[])
    broker.close_position = AsyncMock(
        return_value={"id": "w1", "status": "filled", "filled_avg_price": "0.2"}
    )
    out = await legacy_flatten(
        broker,
        symbols=["BTC/USD", "WIF/USD"],
        dry_run=False,
        actor="mesa",
        tracker=tr,
        flags=flags,
    )
    assert "BTC/USD" in out["rejected"]
    assert [c["symbol"] for c in out["closed"] if not c.get("error")] == ["WIF/USD"]


@pytest.mark.asyncio
async def test_flatten_close_failure_is_not_ok_and_does_not_disarm(session):
    from database.repositories.ops_repository import OpsFlagRepository
    from services.multiasset.crypto_legacy import legacy_flatten
    from services.multiasset.crypto_owned import set_strategy_a_armed, strategy_a_is_armed

    flags = OpsFlagRepository(session)
    await set_strategy_a_armed(flags, armed=True, actor="desk", allocation_usd=2500)
    broker = _paper_broker()
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.get_positions = AsyncMock(
        return_value=[
            {"symbol": "WIF/USD", "qty": "10", "avg_entry_price": "0.2", "asset_class": "crypto"},
        ]
    )
    broker.list_orders = AsyncMock(return_value=[])
    broker.close_position = AsyncMock(side_effect=RuntimeError("404 WIF/USD"))
    out = await legacy_flatten(
        broker, symbols=["WIF/USD"], dry_run=False, actor="mesa", flags=flags
    )
    assert out["ok"] is False
    assert out["error"] == "close_failed"
    assert await strategy_a_is_armed(flags) is True


@pytest.mark.asyncio
async def test_cancel_limited_to_requested_symbols():
    from services.multiasset.crypto_legacy import cancel_open_crypto_orders

    broker = _paper_broker()
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.list_orders = AsyncMock(
        return_value=[
            {"id": "c1", "symbol": "WIF/USD", "asset_class": "crypto", "status": "new"},
            {"id": "c2", "symbol": "BTC/USD", "asset_class": "crypto", "status": "new"},
        ]
    )
    broker.cancel_order = AsyncMock(return_value={})
    out = await cancel_open_crypto_orders(broker, symbols=["WIF/USD"])
    assert {c["id"] for c in out["cancelled"] if not c.get("error")} == {"c1"}
    broker.cancel_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_trade_blocks_mixed_inherited_and_a(session):
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    tr = MultiAssetTradeTracker(session)
    await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.01,
        entry_price=64000.0,
        meta={"note": "inherited august"},
    )
    with pytest.raises(ValueError, match="mixed_lot_blocked"):
        await tr.open_trade(
            desk="crypto",
            symbol="BTC/USD",
            qty=0.001,
            entry_price=100000.0,
            meta={"strategy": "strategy_a", "client_order_id": "sa9-BTCUSD-x"},
        )


@pytest.mark.asyncio
async def test_close_position_uses_unslashed_crypto_symbol():
    from providers.broker.alpaca_provider import AlpacaBrokerProvider

    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=True)
    broker._request = AsyncMock(return_value={"id": "x", "status": "filled"})
    await broker.close_position("WIF/USD")
    path = broker._request.await_args.args[1]
    assert "WIFUSD" in path
    assert "WIF/USD" not in path


@pytest.mark.asyncio
async def test_enable_strategy_a_http_paper_guard_and_inherited(session):
    from fastapi import HTTPException

    from apis.deps import OrgScope
    from apis.routes.ops import EnableStrategyARequest, enable_crypto_strategy_a
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    scope = OrgScope(role="desk", org_id="monarch", user_id="u1", email="ceo@monarch", auth_type="token")
    body = EnableStrategyARequest(confirm=True)

    live = _live_broker()
    with patch("apis.routes.ops._paper_ops_broker", return_value=live):
        with pytest.raises(HTTPException) as exc:
            await enable_crypto_strategy_a(body, session, scope)
        assert exc.value.status_code == 409

    paper = _paper_broker()
    paper.get_positions = AsyncMock(
        return_value=[{"symbol": "BTC/USD", "qty": "0.01", "asset_class": "crypto"}]
    )
    paper.get_account = AsyncMock(return_value={"equity": "10000", "paper": True})
    tr = MultiAssetTradeTracker(session)
    await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.01,
        entry_price=64000.0,
        meta={"note": "inherited"},
    )
    with patch("apis.routes.ops._paper_ops_broker", return_value=paper):
        with pytest.raises(HTTPException) as exc:
            await enable_crypto_strategy_a(body, session, scope)
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "inherited_btc_eth"


@pytest.mark.asyncio
async def test_enable_strategy_a_http_arms_and_persists_allocation(session):
    from apis.deps import OrgScope
    from apis.routes.ops import EnableStrategyARequest, enable_crypto_strategy_a
    from services.multiasset.crypto_owned import strategy_a_is_armed

    scope = OrgScope(role="desk", org_id="monarch", user_id="u1", email="ceo@monarch", auth_type="token")
    paper = _paper_broker()
    paper.get_positions = AsyncMock(return_value=[])
    paper.get_account = AsyncMock(return_value={"equity": "8000", "paper": True})
    with patch("apis.routes.ops._paper_ops_broker", return_value=paper):
        out = await enable_crypto_strategy_a(
            EnableStrategyARequest(confirm=True), session, scope
        )
    from database.repositories.ops_repository import OpsFlagRepository

    assert out["ok"] is True
    assert out["allocation_usd"] == pytest.approx(2000.0)
    assert await strategy_a_is_armed(OpsFlagRepository(session)) is True


@pytest.mark.asyncio
async def test_ops_status_exposes_eligibility_threshold(session):
    from apis.routes.ops import _crypto_paper_status, _strategy_a_eligibility_status

    paper = _paper_broker()
    with patch("apis.routes.ops.get_beta_broker_provider", create=True):
        with patch(
            "services.multiasset.paper_broker.get_beta_broker_provider",
            return_value=paper,
        ):
            thresh = _strategy_a_eligibility_status(paper)
    assert thresh["mode"] == "paper"
    assert thresh["min_trades"] == 10
    assert "paper_guard" in thresh["reason"]
    status = await _crypto_paper_status(session)
    assert "eligibility_threshold" in status
    assert status["eligibility_threshold"]["min_trades"] in {10, 20}


def test_rebalance_up_uses_evidence_gate():
    """n=18 LIVE cannot rebalance; PAPER can. Mirrors autopilot passed_set check."""
    row = _btc_row(n_trades=18, universe=True)
    assert evidence_gate(row, min_trades=20)[0] is False
    assert evidence_gate(row, min_trades=10)[0] is True
    n5 = _btc_row(n_trades=5, universe=True)
    assert evidence_gate(n5, min_trades=10)[0] is False
    assert evidence_gate(n5, min_trades=20)[0] is False
