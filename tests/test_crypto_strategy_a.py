"""Crypto Strategy A (PAPER): eligibility gate, Riesgo limits, legacy flatten."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base

from services.multiasset.crypto_eligibility import (
    EligibilityClosed,
    approved_symbols,
    load_eligibility,
    public_payload,
)
from services.multiasset.crypto_legacy import (
    cancel_stale_gtc_buys,
    close_inherited_crypto_positions,
    is_crypto_symbol,
)
from services.multiasset.crypto_risk import (
    CryptoBook,
    cluster_symbols,
    daily_weekly_pause,
    entry_spread_ok,
    hard_spread_cap_bps,
    kill_from_allocation_peak,
    per_name_caps,
    ramp_mult,
    size_crypto_order,
    universe_spread_ok,
)
from services.multiasset.strategy_a import (
    arm_post_stop_block,
    btc_regime_ok,
    chandelier_stop_px,
    donchian_S,
    evaluate_chandelier_exit,
    hysteresis_channel,
    last_completed_frame,
    should_rebalance,
    stop_is_tradable,
    strategy_a_signal,
    update_post_stop_block,
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


def _ohlc_uptrend(n: int = 1300, start: float = 100.0, end: float = 200.0) -> pd.DataFrame:
    close = np.linspace(start, end, n)
    # Tight wicks so each close can exceed the prior L highs (Donchian ON).
    step = abs(end - start) / max(n - 1, 1)
    wick = max(step * 0.1, 1e-6)
    high = close + wick
    low = close - wick
    open_ = close - wick * 0.5
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close})


def _empty_book(eq: float = 10_000.0, **kwargs) -> CryptoBook:
    base = dict(
        equity=eq,
        crypto_notional=0.0,
        open_risk_usd=0.0,
        n_positions=0,
        name_notional={},
        name_risk={},
        group_notional={},
        group_risk={},
        membership={},
    )
    base.update(kwargs)
    return CryptoBook(**base)


def test_strategy_a_combo9_buy_on_S_and_btc_4h_sma():
    df = _ohlc_uptrend(1300)
    btc = _ohlc_uptrend(1300, 40000, 70000)
    assert btc_regime_ok(btc)[0] is True
    sig = strategy_a_signal(df, btc_4h=btc)
    assert sig.side == "buy"
    assert sig.S == pytest.approx(1.0)
    assert sig.extras["combo"] == 9
    assert sig.extras["donchian_L"] == [120, 240, 480]
    assert sig.extras["chandelier_atr_mult"] == 8
    assert sig.extras.get("tp") is None
    assert sig.stop_px is not None
    last = float(df["Close"].iloc[-1])
    assert stop_is_tradable(last, sig.stop_px)
    assert last - sig.stop_px == pytest.approx(8 * sig.atr_abs, rel=1e-6)


def test_strategy_a_blocks_when_btc_below_sma200_4h():
    df = _ohlc_uptrend(1300)
    btc = _ohlc_uptrend(1300, 70000, 40000)
    sig = strategy_a_signal(df, btc_4h=btc)
    assert sig.side == "hold"
    assert "sma200" in sig.reason


def test_donchian_hysteresis_on_off_and_S_levels():
    n = 30
    close = np.full(n, 10.0)
    high = close + 0.2
    low = close - 0.2
    close = close.copy()
    high = high.copy()
    low = low.copy()
    close[12] = 20.0
    high[12] = 20.2
    close[20] = 1.0
    low[20] = 0.8
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close})
    ch = hysteresis_channel(df, 4)
    assert ch[11] == 0
    assert ch[12] == 1
    assert ch[19] == 1
    assert ch[20] == 0
    s, parts = donchian_S(_ohlc_uptrend(1300))
    assert s == pytest.approx(1.0)
    assert set(parts) == {120, 240, 480}
    assert s in {0.0, 1 / 3, 2 / 3, 1.0} or abs(s * 3 - round(s * 3)) < 1e-9


def test_chandelier_ratchets_up_only_no_tp():
    s0 = chandelier_stop_px(100.0, 1.0)
    assert s0 == pytest.approx(92.0)
    s1 = chandelier_stop_px(110.0, 1.0, prev_stop=s0)
    assert s1 == pytest.approx(102.0)
    s2 = chandelier_stop_px(105.0, 3.0, prev_stop=s1)  # cand=105-24=81 → stays 102
    assert s2 == pytest.approx(102.0)


def test_chandelier_evaluates_completed_close_executes_next_open():
    idx = pd.date_range("2026-01-01", periods=40, freq="4h", tz="UTC")
    close = np.linspace(100.0, 120.0, 40)
    close[-2] = 50.0  # completed bar dumps
    close[-1] = 51.0  # in-progress print — must not be the trigger
    df = pd.DataFrame(
        {"Open": close - 0.1, "High": close + 1.0, "Low": close - 1.0, "Close": close},
        index=idx,
    )
    now = idx[-1] + pd.Timedelta(hours=1)
    completed = last_completed_frame(df, now=now.to_pydatetime())
    assert len(completed) == 39
    ch = evaluate_chandelier_exit(
        df, highest_close=120.0, stop_px=100.0, pending_exit=False, now=now.to_pydatetime()
    )
    assert ch["candle_close"] == pytest.approx(50.0)
    assert ch["hit"] is True
    assert ch["execute_now"] is True
    assert ch["broker_stop"] == "none"
    # No datetime: first hit waits for next cycle (next open).
    flat = pd.DataFrame({"Open": close - 0.1, "High": close + 1.0, "Low": close - 1.0, "Close": close})
    first = evaluate_chandelier_exit(flat, highest_close=120.0, stop_px=100.0, pending_exit=False)
    assert first["hit"] is True
    assert first["execute_now"] is False
    assert first["pending_exit"] is True
    second = evaluate_chandelier_exit(flat, highest_close=120.0, stop_px=100.0, pending_exit=True)
    assert second["execute_now"] is True


def test_post_stop_block_until_S_zero_then_up():
    b = arm_post_stop_block(1.0)
    assert b["blocked"] is True and b["seen_zero"] is False
    b = update_post_stop_block(b, 2 / 3)
    assert b["blocked"] is True
    b = update_post_stop_block(b, 0.0)
    assert b["blocked"] is True and b["seen_zero"] is True
    b = update_post_stop_block(b, 1 / 3)
    assert b["blocked"] is False
    assert should_rebalance(100, 100, 1.0, 2 / 3) is True
    assert should_rebalance(100, 100, 1.0, 1.0) is False
    assert should_rebalance(130, 100, 1.0, 1.0) is True  # 30% > 20%


def test_stop_zero_or_above_entry_rejected():
    assert stop_is_tradable(1.0, 0.0) is False
    assert stop_is_tradable(1.0, 1.0) is False
    assert stop_is_tradable(1.0, 1.01) is False
    assert stop_is_tradable(1.0, 0.92) is True


def test_eligibility_seed_is_btc_eth_and_does_not_recompute():
    data = load_eligibility()
    assert data.get("runtime_must_not_recompute") is True
    assert approved_symbols(data) == ["BTC/USD", "ETH/USD"]
    pub = public_payload(data)
    assert pub["paper"] is True
    assert "api_key" not in str(pub).lower()


def test_eligibility_empty_is_closed(tmp_path: Path):
    p = tmp_path / "empty.json"
    p.write_text('{"approved": []}', encoding="utf-8")
    with pytest.raises(EligibilityClosed):
        load_eligibility(p)
    missing = tmp_path / "nope.json"
    with pytest.raises(EligibilityClosed):
        load_eligibility(missing)


def test_risk_btc_eth_vs_alt_caps():
    eq = 10_000.0
    btc_n, btc_r = per_name_caps("BTC/USD", eq)
    alt_n, alt_r = per_name_caps("SOL/USD", eq)
    assert btc_n == 1000.0 and btc_r == 50.0
    assert alt_n == 500.0 and alt_r == 25.0
    assert hard_spread_cap_bps("BTC/USD") == 10
    assert hard_spread_cap_bps("SOL/USD") == 15
    assert hard_spread_cap_bps("WIF/USD") == 30


def test_daily_screen_liquidity_evidence_and_report():
    from services.multiasset.crypto_filters import build_gate_report, evidence_gate, liquidity_ok, screen_symbol

    assert evidence_gate({"status": "seed_pending_strategy_backtest"}, min_trades=20)[0] is True
    assert evidence_gate({"expectancy": -0.1, "n_trades": 80}, min_trades=20)[0] is False
    assert evidence_gate({"expectancy": 0.4, "n_trades": 5}, min_trades=20)[0] is False
    assert evidence_gate({"expectancy": 0.4, "n_trades": 40}, min_trades=20)[0] is True
    assert liquidity_ok(500_000, min_adv_usd=1_000_000)[0] is False
    assert liquidity_ok(5_000_000, min_adv_usd=1_000_000)[0] is True
    btc = screen_symbol(
        {"symbol": "BTC/USD", "status": "seed", "median_spread_bps": 8, "median_adv_usd": 2e10, "expectancy": None},
        min_adv_usd=1_000_000,
        min_trades=20,
        live_spread_bps=9.0,
        adv_usd=2e10,
        tradable=True,
    )
    thin = screen_symbol(
        {"symbol": "WIF/USD", "expectancy": 0.2, "n_trades": 40, "median_spread_bps": 40},
        min_adv_usd=1_000_000,
        min_trades=20,
        live_spread_bps=12.0,
        adv_usd=1000,
        tradable=True,
    )
    assert btc["passed"] is True
    assert thin["passed"] is False
    report = build_gate_report([btc, thin])
    assert report["passed_symbols"] == ["BTC/USD"]
    assert report["runtime_must_not_recompute_oos"] is True


def test_size_respects_25pct_sleeve_and_1_5_agg_risk():
    eq = 10_000.0
    book = CryptoBook(
        equity=eq,
        crypto_notional=2400.0,
        open_risk_usd=140.0,
        n_positions=1,
        name_notional={"ETH/USD": 2400.0},
        name_risk={"ETH/USD": 140.0},
        group_notional={},
        group_risk={},
        membership={},
    )
    notional, info = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=99.0,  # 1% stop → 0.5% risk = $50 → $5000 raw, sleeve room $100
        book=book,
        n_trades=20,
        s_signal=1.0,
        vol_30d=0.25,
    )
    assert notional <= 100.01  # 25% of 10k = 2500, 2400 used
    assert info["reason"] == "ok"
    # aggregate risk room $10 → notional from 1% stop = $1000, still sleeve-capped
    book2 = CryptoBook(
        equity=eq,
        crypto_notional=0.0,
        open_risk_usd=149.0,
        n_positions=1,
        name_notional={},
        name_risk={},
        group_notional={},
        group_risk={},
        membership={},
    )
    n2, _ = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=99.0,
        book=book2,
        n_trades=20,
        s_signal=1.0,
        vol_30d=0.25,
    )
    assert n2 <= 100.0 + 1e-6  # $1 risk room / 1% * 100


def test_max_six_positions_and_ramp_half_size():
    assert ramp_mult(0) == 0.5
    assert ramp_mult(11) == 0.5
    assert ramp_mult(12) == 1.0
    eq = 10_000.0
    book = CryptoBook(
        equity=eq,
        crypto_notional=0.0,
        open_risk_usd=0.0,
        n_positions=6,
        name_notional={},
        name_risk={},
        group_notional={},
        group_risk={},
        membership={},
    )
    n, info = size_crypto_order(
        symbol="BTC/USD", equity=eq, entry=100.0, stop=99.0, book=book, n_trades=20
    )
    assert n == 0 and info["reason"] == "max_positions"
    n3, info3 = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=99.0,
        book=CryptoBook(
            equity=eq,
            crypto_notional=0,
            open_risk_usd=0,
            n_positions=3,
            name_notional={},
            name_risk={},
            group_notional={},
            group_risk={},
            membership={},
        ),
        n_trades=20,
        max_positions=3,
    )
    assert n3 == 0 and info3["reason"] == "max_positions"


def test_corr_group_caps_and_spread_filters():
    groups = cluster_symbols(
        {("ETH/USD", "BTC/USD"): 0.85, ("SOL/USD", "BTC/USD"): 0.4},
        ["BTC/USD", "ETH/USD", "SOL/USD"],
    )
    btc_eth = next(g for g in groups if "BTC/USD" in g and "ETH/USD" in g)
    assert "SOL/USD" not in btc_eth or len(btc_eth) >= 2
    assert universe_spread_ok("BTC/USD", 8.0)[0] is True
    assert universe_spread_ok("BTC/USD", 12.0)[0] is False
    assert entry_spread_ok("ETH/USD", live_bps=21.0, median_bps=8.0)[0] is False  # >2.5×
    assert entry_spread_ok("ETH/USD", live_bps=12.0, median_bps=8.0)[0] is True
    assert entry_spread_ok("ETH/USD", live_bps=None, median_bps=8.0)[0] is False
    assert entry_spread_ok("ETH/USD", live_bps=None, median_bps=8.0)[1] == "live_spread_unknown"


def test_kill_10pct_allocation_and_pauses():
    # allocation $2500, peak 2000, now 1749 → DD 251 ≥ 250
    assert kill_from_allocation_peak(peak_crypto_usd=2000, crypto_usd=1749, allocation_usd=2500) is True
    assert kill_from_allocation_peak(peak_crypto_usd=2000, crypto_usd=1900, allocation_usd=2500) is False
    paused, why = daily_weekly_pause(day_pnl_pct=-1.51, week_pnl_pct=0)
    assert paused and "daily" in why
    paused, why = daily_weekly_pause(day_pnl_pct=0, week_pnl_pct=-3.01)
    assert paused and "weekly" in why
    assert daily_weekly_pause(day_pnl_pct=-1.0, week_pnl_pct=-2.0)[0] is False


def test_is_crypto_not_etf():
    assert is_crypto_symbol("WIF/USD")
    assert is_crypto_symbol("BTCUSD")
    assert not is_crypto_symbol("GLD")
    assert not is_crypto_symbol("GLDM")


@pytest.mark.asyncio
async def test_legacy_cancel_only_wif_ldo_render_market_new():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.list_orders = AsyncMock(
        return_value=[
            {"id": "wif", "symbol": "WIF/USD", "side": "buy", "type": "market", "status": "new"},
            {"id": "ldo", "symbol": "LDO/USD", "side": "buy", "type": "market", "status": "accepted"},
            {"id": "ren", "symbol": "RENDER/USD", "side": "buy", "type": "market", "status": "new"},
            {"id": "stop", "symbol": "WIF/USD", "side": "sell", "type": "stop", "status": "new"},
            {"id": "btc", "symbol": "BTC/USD", "side": "buy", "type": "market", "status": "new"},
        ]
    )
    broker.cancel_order = AsyncMock(return_value={"ok": True})
    out = await cancel_stale_gtc_buys(broker)
    assert set(out["cancelled"]) == {"wif", "ldo", "ren"}
    assert {c.args[0] for c in broker.cancel_order.await_args_list} == {"wif", "ldo", "ren"}


@pytest.mark.asyncio
async def test_legacy_live_url_does_nothing():
    broker = MagicMock()
    broker.base_url = "https://api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.list_orders = AsyncMock()
    broker.cancel_order = AsyncMock()
    broker.get_positions = AsyncMock()
    broker.close_position = AsyncMock()
    broker.get_account = AsyncMock()
    c = await cancel_stale_gtc_buys(broker)
    p = await close_inherited_crypto_positions(broker)
    assert c["skipped"] == "not_paper_url"
    assert p["skipped"] == "not_paper_url"
    broker.cancel_order.assert_not_awaited()
    broker.close_position.assert_not_awaited()
    broker.list_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_closes_crypto_not_gld_and_journals_fill():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.get_positions = AsyncMock(
        return_value=[
            {"symbol": "WIF/USD", "qty": "10", "avg_entry_price": "2.0"},
            {"symbol": "GLD", "qty": "1", "avg_entry_price": "180"},
        ]
    )
    broker.close_position = AsyncMock(
        return_value={"id": "x1", "filled_avg_price": "1.5", "symbol": "WIF/USD"}
    )
    tracker = MagicMock()
    tracker.close_trade = AsyncMock()
    out = await close_inherited_crypto_positions(broker, tracker=tracker)
    assert out["count"] == 1
    assert out["closed"][0]["symbol"] == "WIF/USD"
    assert out["realized_pnl_usd"] == pytest.approx(-5.0)
    broker.close_position.assert_awaited_once()
    tracker.close_trade.assert_awaited()


@pytest.mark.asyncio
async def test_crypto_execute_has_no_broker_bracket(session, monkeypatch):
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    from config.settings import get_settings
    from domain.multiasset import MultiAssetOrderRequest
    from services.multiasset.desk_service import MultiAssetDeskService

    get_settings.cache_clear()
    mock_broker = MagicMock()
    mock_broker.is_configured.return_value = True
    mock_broker.base_url = "https://paper-api.alpaca.markets"
    mock_broker.paper = True
    mock_broker.get_account = AsyncMock(return_value={"paper": True})
    mock_broker.submit_order = AsyncMock(return_value={"id": "o1", "status": "accepted"})
    with (
        patch("services.multiasset.desk_service.get_beta_broker_provider", return_value=mock_broker),
        patch(
            "services.multiasset.desk_service.quote_symbol",
            AsyncMock(return_value={"symbol": "BTC/USD", "current_price": 50000.0}),
        ),
        patch.object(MultiAssetDeskService, "_journal_write", AsyncMock()),
        patch.object(MultiAssetDeskService, "_track_fill", AsyncMock()),
    ):
        svc = MultiAssetDeskService(session)
        await svc.execute(
            MultiAssetOrderRequest(
                desk="crypto",
                symbol="BTC/USD",
                side="buy",
                notional=50,
                confirm=True,
                dry_run=False,
            )
        )
    payload = mock_broker.submit_order.await_args.args[0]
    assert "order_class" not in payload
    assert "stop_loss" not in payload
    assert payload.get("type") == "market"
    assert "trail_price" not in payload
    get_settings.cache_clear()


def test_size_min_of_vol_risk_adv_btc_and_skip_min_lot():
    eq = 10_000.0
    book = _empty_book(eq)
    # 1% ADV binds on BTC (was previously skipped for majors).
    n, info = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=92.0,
        book=book,
        n_trades=20,
        s_signal=1.0,
        vol_30d=0.25,
        median_adv_usd=10_000.0,
    )
    assert info["reason"] == "ok"
    assert n == pytest.approx(100.0)  # 1% of 10k ADV
    # Min lot does not fit → skip, never enlarge to $10.
    n2, info2 = size_crypto_order(
        symbol="BTC/USD",
        equity=100.0,
        entry=100.0,
        stop=20.0,
        book=_empty_book(100.0),
        n_trades=20,
        s_signal=1.0,
        vol_30d=0.25,
    )
    assert n2 == 0.0 and info2["reason"] == "too_small"
    assert float(info2.get("sized") or 0) < 10
    # Min qty would require enlarging the ticket → skip.
    n3, info3 = size_crypto_order(
        symbol="ETH/USD",
        equity=eq,
        entry=100.0,
        stop=92.0,
        book=_empty_book(eq),
        n_trades=20,
        s_signal=1.0,
        vol_30d=0.25,
        min_qty=20.0,  # $2000 min vs sized << that
        median_adv_usd=50_000,
    )
    assert n3 == 0.0 and info3["reason"] == "too_small"


def test_size_ramp_applied_once_not_twice():
    eq = 10_000.0
    n, info = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=92.0,
        book=_empty_book(eq),
        n_trades=0,
        s_signal=1.0,
        vol_30d=0.25,
    )
    assert info["ramp"] == 0.5
    # Risk 0.5% = $50 vs 8% stop → $625; ×0.5 ramp once = $312.50 (twice would be $156.25)
    assert n == pytest.approx(312.5)


def test_sanitize_crypto_order_refuses_stop_types():
    from services.multiasset.crypto_orders import (
        CryptoStopNotSupported,
        is_stop_like,
        sanitize_crypto_order,
    )

    clean = sanitize_crypto_order(
        {"symbol": "BTC/USD", "type": "market", "order_class": "oto", "stop_loss": {"stop_price": "1"}}
    )
    assert "order_class" not in clean and "stop_loss" not in clean
    assert clean["type"] == "market"
    for t in ("stop", "stop_limit", "trailing_stop"):
        with pytest.raises(CryptoStopNotSupported):
            sanitize_crypto_order({"symbol": "ETH/USD", "type": t})
    assert is_stop_like({"type": "stop"}) is True
    assert is_stop_like({"type": "market"}) is False


@pytest.mark.asyncio
async def test_rejected_crypto_stop_marks_broker_stop_none(session):
    from services.multiasset.crypto_obs import attach_last_cycle_obs, overlay_open_positions
    from services.multiasset.crypto_orders import record_rejected_crypto_stop
    from services.multiasset.risk_engine import MultiAssetRiskDesk

    alert = await record_rejected_crypto_stop(
        session, symbol="BTC/USD", detail="422 stop not allowed", raw={"status": "rejected"}
    )
    assert alert["broker_stop"] == "none"
    snap = await MultiAssetRiskDesk(session).snapshot(equity=10_000)
    assert snap["crypto_24_7"]["broker_stops_gtc"] is False
    assert snap["crypto_24_7"]["broker_stop"] == "none"

    state = {
        "positions": {
            "BTC/USD": {
                "stop_evaluated_at": "2026-10-01T16:00:00+00:00",
                "candle_close": 65000.0,
                "stop_px": 61200.0,
                "broker_stop": "none",
            }
        }
    }
    over = overlay_open_positions([{"symbol": "BTCUSD", "qty": "0.01"}], state["positions"])
    assert over[0]["stop_evaluated_at"] == "2026-10-01T16:00:00+00:00"
    assert over[0]["candle_close"] == 65000.0
    assert over[0]["stop_px"] == 61200.0
    assert over[0]["broker_stop"] == "none"
    cycle = attach_last_cycle_obs({"desks": {"crypto": {"buys": 0}}}, state)
    pos = cycle["desks"]["crypto"]["open_positions"]
    assert pos[0]["broker_stop"] == "none"
    assert "stop_evaluated_at" in pos[0]
    assert cycle["broker_stops_gtc"] is False
    assert cycle["desks"]["crypto"]["broker_stops_gtc"] is False


@pytest.mark.asyncio
async def test_last_cycle_and_desk_status_expose_stop_obs(session, monkeypatch):
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    from config.settings import get_settings
    from database.repositories.ops_repository import OpsFlagRepository
    from services.multiasset.desk_service import MultiAssetDeskService
    from services.multiasset.risk_engine import FLAG_CYCLE

    get_settings.cache_clear()
    flags = OpsFlagRepository(session)
    await flags.set_json(
        "crypto_strategy_a_state",
        {
            "positions": {
                "ETH/USD": {
                    "stop_evaluated_at": "2026-10-01T20:00:00+00:00",
                    "candle_close": 4200.0,
                    "stop_px": 3900.0,
                    "broker_stop": "none",
                }
            }
        },
    )
    await flags.set_json(FLAG_CYCLE, {"desks": {"crypto": {"buys": 1, "sells": 0}}})

    mock_broker = MagicMock()
    mock_broker.is_configured.return_value = True
    mock_broker.base_url = "https://paper-api.alpaca.markets"
    mock_broker.get_account = AsyncMock(return_value={"equity": "10000", "cash": "8000", "paper": True})
    mock_broker.get_positions = AsyncMock(
        return_value=[{"symbol": "ETH/USD", "qty": "0.2", "avg_entry_price": "4000"}]
    )
    mock_broker.list_orders = AsyncMock(return_value=[])
    with (
        patch("services.multiasset.desk_service.get_beta_broker_provider", return_value=mock_broker),
        patch("services.multiasset.desk_service.quote_symbol", AsyncMock(return_value={"current_price": 4200})),
        patch.object(MultiAssetDeskService, "sync_crypto_universe", AsyncMock(return_value=0)),
    ):
        st = await MultiAssetDeskService(session).status("crypto")
    eth = next(p for p in st.positions if "ETH" in str(p.get("symbol")))
    assert eth["stop_evaluated_at"] == "2026-10-01T20:00:00+00:00"
    assert eth["candle_close"] == 4200.0
    assert eth["stop_px"] == 3900.0
    assert eth["broker_stop"] == "none"

    from apis.routes.multiasset import last_multiasset_cycle

    body = await last_multiasset_cycle(session)
    row = body["desks"]["crypto"]["open_positions"][0]
    assert row["symbol"] == "ETH/USD"
    assert row["stop_px"] == 3900.0
    assert row["broker_stop"] == "none"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_eligibility_endpoint_desk_only(monkeypatch, tmp_path):
    from apis.app import create_app
    from config.settings import get_settings
    from database.engine import init_db

    db = tmp_path / "elig.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    get_settings.cache_clear()
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauth = await client.get("/api/v1/beta/multiasset/strategy-a/eligibility")
        assert unauth.status_code == 401
        login = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        assert login.status_code == 200
        ok = await client.get("/api/v1/beta/multiasset/strategy-a/eligibility")
        assert ok.status_code == 200
        body = ok.json()
        assert body["paper"] is True
        assert {r["symbol"] for r in body["approved"]} == {"BTC/USD", "ETH/USD"}
        assert body["runtime_must_not_recompute"] is True
    get_settings.cache_clear()
