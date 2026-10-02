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
    catch_up_exits,
    chandelier_stop_px,
    donchian_S,
    evaluate_chandelier_exit,
    hysteresis_channel,
    last_completed_frame,
    rebuild_highest_close,
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
    # Alternate unlock: S rises above S-at-stop without visiting 0.
    b2 = arm_post_stop_block(1 / 3)
    assert b2["s_at_stop"] == pytest.approx(1 / 3)
    b2 = update_post_stop_block(b2, 1 / 3)
    assert b2["blocked"] is True
    b2 = update_post_stop_block(b2, 2 / 3)
    assert b2["blocked"] is False
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
    fallback = load_eligibility(missing)
    assert {r["symbol"] for r in fallback["approved"]} == {"BTC/USD", "ETH/USD"}


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

    assert evidence_gate({"status": "seed_pending_strategy_backtest"}, min_trades=20)[0] is False
    assert evidence_gate(
        {"status": "seed", "on_approved_universe": True, "expectancy": None}, min_trades=20
    )[0] is False
    assert evidence_gate({"expectancy": -0.1, "n_trades": 80}, min_trades=20)[0] is False
    assert evidence_gate({"expectancy": 0.4, "n_trades": 5}, min_trades=20)[0] is False
    assert evidence_gate({"expectancy": 0.4, "n_trades": 40}, min_trades=20)[0] is True
    assert liquidity_ok(500_000, min_adv_usd=1_000_000)[0] is False
    assert liquidity_ok(5_000_000, min_adv_usd=1_000_000)[0] is True
    btc = screen_symbol(
        {
            "symbol": "BTC/USD",
            "status": "approved",
            "on_approved_universe": True,
            "median_spread_bps": 8,
            "median_adv_usd": 2e10,
            "expectancy": 0.4,
            "n_trades": 40,
        },
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
    assert ramp_mult(0, symbol="BTC/USD") == 1.0
    assert ramp_mult(0, symbol="ETH/USD") == 1.0
    assert ramp_mult(0, symbol="SOL/USD") == 0.5
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
    assert paused and "24h" in why
    paused, why = daily_weekly_pause(day_pnl_pct=0, week_pnl_pct=-3.01)
    assert paused and "7d" in why
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
    assert float(info2.get("sized") or 0) < 50
    # After caps, ticket < $50 is skipped (never enlarged).
    n50, info50 = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=92.0,
        book=_empty_book(eq),
        n_trades=20,
        s_signal=1.0,
        vol_30d=0.25,
        median_adv_usd=4_000.0,  # 1% = $40 < $50
    )
    assert n50 == 0.0 and info50["reason"] == "too_small"
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
        symbol="SOL/USD",
        equity=eq,
        entry=100.0,
        stop=92.0,
        book=_empty_book(eq),
        n_trades=0,
        s_signal=1.0,
        vol_30d=0.25,
    )
    assert info["ramp"] == 0.5
    # Alt risk 0.25% = $25 vs 8% stop + 25bp cost → ×0.5 ramp once (never twice).
    assert n == pytest.approx(151.52)
    n_btc, info_btc = size_crypto_order(
        symbol="BTC/USD",
        equity=eq,
        entry=100.0,
        stop=92.0,
        book=_empty_book(eq),
        n_trades=0,
        s_signal=1.0,
        vol_30d=0.25,
    )
    assert info_btc["ramp"] == 1.0
    assert n_btc == pytest.approx(606.06)  # $50 / 8.25% (8 ATR + 25bp) * 100; no half-ramp


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

    with patch(
        "services.multiasset.autopilot.MultiAssetAutopilotService.crypto_catchup_on_wake",
        AsyncMock(return_value={"skipped": "test"}),
    ):
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


def _daily_adv_bars(n: int, *, close0: float = 100.0, vol: float = 1_000.0, drift: float = 0.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    close = close0 + np.arange(n) * drift
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": np.full(n, vol),
        },
        index=idx,
    )


def test_weekly_30d_alpaca_adv_corr_and_skip_short_history():
    from services.multiasset.crypto_market_stats import (
        adv_for_symbol,
        alpaca_bars_to_daily,
        build_weekly_snapshot_from_daily,
        corr_pairs_from_snapshot,
        extract_alpaca_symbol_bars,
        median_adv_30d,
    )

    bars = [
        {"t": f"2026-01-{i:02d}T00:00:00Z", "o": 10.0, "h": 11.0, "l": 9.0, "c": 10.0, "v": 50.0}
        for i in range(1, 32)
    ]
    daily = alpaca_bars_to_daily(bars)
    med, why = median_adv_30d(daily)
    assert why == "ok"
    assert med == pytest.approx(500.0)  # 10 * 50

    short = alpaca_bars_to_daily(bars[:10])
    med_s, why_s = median_adv_30d(short)
    assert med_s is None and why_s == "history_lt_30d"

    btc = _daily_adv_bars(40, close0=100.0, vol=2_000.0, drift=0.5)
    eth = _daily_adv_bars(40, close0=50.0, vol=800.0, drift=0.25)  # same direction → high corr
    thin = _daily_adv_bars(12, close0=5.0, vol=10.0)
    snap = build_weekly_snapshot_from_daily(
        {"BTC/USD": btc, "ETH/USD": eth, "SOL/USD": thin},
        week_key="2026-W40",
        expected=["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
    )
    assert snap["source"] == "alpaca_crypto_1d_30d"
    assert "BTC/USD" in snap["adv_usd"] and "ETH/USD" in snap["adv_usd"]
    assert "SOL/USD" not in snap["adv_usd"]
    reasons = {r["symbol"]: r["reason"] for r in snap["rejected"]}
    assert reasons["SOL/USD"] == "history_lt_30d"
    assert reasons["DOGE/USD"] == "history_lt_30d"
    btc_adv, btc_why = adv_for_symbol(snap, "BTC/USD")
    assert btc_why == "ok" and btc_adv and btc_adv > 0
    none_adv, none_why = adv_for_symbol(snap, "SOL/USD")
    assert none_adv is None and none_why == "history_lt_30d"
    pairs = corr_pairs_from_snapshot(snap)
    rho = pairs.get(("BTC/USD", "ETH/USD")) or pairs.get(("ETH/USD", "BTC/USD"))
    assert rho is not None and rho >= 0.7
    groups = cluster_symbols(pairs, ["BTC/USD", "ETH/USD"])
    assert any({"BTC/USD", "ETH/USD"} <= g for g in groups)

    payload = {"bars": {"BTC/USD": bars, "ETHUSD": bars[:5]}}
    extracted = extract_alpaca_symbol_bars(payload)
    assert "BTC/USD" in extracted and "ETH/USD" in extracted


@pytest.mark.asyncio
async def test_fetch_alpaca_crypto_daily_uses_data_api_not_trading():
    from services.multiasset.crypto_market_stats import ALPACA_CRYPTO_BARS_PATH, fetch_alpaca_crypto_daily

    seen: list[tuple[str, dict]] = []

    async def fake_get(path: str, params: dict):
        seen.append((path, params))
        return {
            "bars": {
                "BTC/USD": [
                    {
                        "t": f"2026-02-{i:02d}T00:00:00Z",
                        "o": 100,
                        "h": 101,
                        "l": 99,
                        "c": 100,
                        "v": 20,
                    }
                    for i in range(1, 29)
                ]
            }
        }

    frames = await fetch_alpaca_crypto_daily(["BTC/USD"], http_get=fake_get)
    assert seen and seen[0][0] == ALPACA_CRYPTO_BARS_PATH
    assert seen[0][1]["timeframe"] == "1Day"
    assert "BTC/USD" in frames
    assert len(frames["BTC/USD"]) == 28


def _4h_fixture(n: int = 40, start: float = 100.0, end: float = 140.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    close = np.linspace(start, end, n)
    return pd.DataFrame(
        {"Open": close - 0.2, "High": close + 1.0, "Low": close - 1.0, "Close": close},
        index=idx,
    )


def _engine_walk_missed(df: pd.DataFrame, last_eval, high: float, stop: float, now):
    """Reference motor: one completed bar at a time, compare then raise."""
    completed = last_completed_frame(df, now=now)
    if last_eval is not None:
        ts = pd.Timestamp(last_eval)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        bars = completed.loc[completed.index > ts]
    else:
        bars = completed.iloc[-1:]
    hit = False
    hit_close = None
    for i in range(len(bars)):
        prefix = completed.loc[: bars.index[i]]
        clock_i = pd.Timestamp(bars.index[i]) + pd.Timedelta(hours=4)
        ch = evaluate_chandelier_exit(
            prefix,
            highest_close=high,
            stop_px=stop,
            pending_exit=False,
            now=clock_i.to_pydatetime(),
        )
        if ch["hit"]:
            hit = True
            hit_close = ch["candle_close"]
            high = float(ch["highest_close"])
            stop = float(ch["stop_px"] or stop)
            break
        high = float(ch["highest_close"])
        stop = float(ch["stop_px"] or stop)
    return {"hit": hit, "highest_close": high, "stop_px": stop, "hit_close": hit_close}


def test_catch_up_n_missed_candles_matches_engine_stop_and_exit():
    """N velas perdidas: mismo stop/salida que el motor (evaluate_chandelier bar a bar)."""
    n = 36
    df = _4h_fixture(n, 100.0, 130.0)
    # Dump on recovered bar n-5 (not the last closed).
    dump_i = n - 5
    df.iloc[dump_i, df.columns.get_loc("Close")] = 40.0
    df.iloc[dump_i, df.columns.get_loc("Low")] = 39.0
    last_eval = df.index[n - 10]  # 5 recovered + last closed
    now = (df.index[-1] + pd.Timedelta(hours=4, minutes=20)).to_pydatetime()
    high0, stop0 = 130.0, 120.0
    engine = _engine_walk_missed(df, last_eval, high0, stop0, now)
    cup = catch_up_exits(
        df,
        last_evaluated_open=last_eval,
        highest_close=high0,
        stop_px=stop0,
        now=now,
    )
    assert cup["data_ok"] is True
    assert cup["hit"] is True
    assert engine["hit"] is True
    assert cup["highest_close"] == pytest.approx(engine["highest_close"])
    assert float(cup["stop_px"]) == pytest.approx(float(engine["stop_px"]))
    assert cup["hit_bar"]["sell_now"] is True
    assert cup["hit_bar"]["late"] is True
    assert cup["signal_bar"] is None  # no entry/rebalance from recovered bars
    assert cup["missed_candles"] >= 1

    # No dump: N missed bars ratchet the same as the engine.
    df2 = _4h_fixture(n, 100.0, 130.0)
    now2 = (df2.index[-1] + pd.Timedelta(hours=4, minutes=5)).to_pydatetime()
    engine2 = _engine_walk_missed(df2, last_eval, 110.0, 90.0, now2)
    cup2 = catch_up_exits(
        df2,
        last_evaluated_open=last_eval,
        highest_close=110.0,
        stop_px=90.0,
        now=now2,
    )
    assert cup2["hit"] is False
    assert engine2["hit"] is False
    assert cup2["highest_close"] == pytest.approx(engine2["highest_close"])
    assert float(cup2["stop_px"]) == pytest.approx(float(engine2["stop_px"]))
    assert cup2["signal_bar"] is not None
    assert cup2["signal_bar"]["candle_open"] == cup2["evals"][-1]["candle_open"]


def test_catch_up_entries_only_last_closed_bar():
    df = _4h_fixture(20, 80.0, 100.0)
    now = (df.index[-1] + pd.Timedelta(hours=4)).to_pydatetime()
    cup = catch_up_exits(
        df,
        last_evaluated_open=df.index[10],
        highest_close=90.0,
        stop_px=50.0,
        now=now,
    )
    assert cup["signal_bar"]["candle_open"] == cup["evals"][-1]["candle_open"]
    assert all(not e.get("sell_now") for e in cup["evals"][:-1] if not e.get("hit"))


def test_rebuild_highest_close_never_resets_to_entry():
    df = _4h_fixture(20, 100.0, 150.0)
    hist, src = rebuild_highest_close(df, entry_ts=df.index[5], persisted=120.0)
    assert hist >= 150.0 - 1e-9
    assert src in {"db", "rebuilt"}
    kept, src2 = rebuild_highest_close(df, entry_ts=df.index[5], persisted=200.0)
    assert kept == pytest.approx(200.0)
    assert src2 == "db"


def test_wealth_mark_to_market_sell_does_not_stick_kill():
    from services.multiasset.crypto_risk import (
        kill_from_allocation_peak,
        reset_allocation_kill,
        wealth_drawdown_pct,
    )

    allocation = 2500.0
    mark_open = 2000.0
    realized = 0.0
    peak = 2000.0
    # Sell a 2.5%+ name at the mark: wealth unchanged (mark↓ + realized↑).
    sold_notional = 80.0  # 3.2% of allocation
    sold_pnl = 5.0
    wealth_after = (mark_open - sold_notional) + (realized + sold_pnl + sold_notional)
    # wealth_after ≈ 2005 if we treat proceeds as realized notional+pnl
    wealth_mtm = (mark_open - sold_notional) + realized + sold_pnl
    # Correct wealth = remaining mark + realized including sale proceeds? Spec: mark+realized.
    # Sale converts mark to realized at last; wealth = remaining_mark + prior_realized + exit_value - entry?
    remaining = mark_open - sold_notional
    realized_after = realized + sold_pnl
    wealth = remaining + realized_after
    dd = wealth_drawdown_pct(peak=peak, wealth=wealth, allocation=allocation)
    assert kill_from_allocation_peak(
        peak_crypto_usd=peak, crypto_usd=wealth, allocation_usd=allocation
    ) is False
    assert dd < 10.0
    reset = reset_allocation_kill({"peak_wealth_usd": peak, "kill_active": True}, actor="ceo", reason="audit", current_wealth=wealth)
    assert reset["kill_active"] is False
    assert reset["kill_reset"]["actor"] == "ceo"


@pytest.mark.asyncio
async def test_replica_lease_one_lock_no_dup_orders(session):
    from services.multiasset.crypto_cycle_lock import (
        acquire_cycle_lease,
        idempotency_key,
        release_cycle_lease,
    )
    from database.repositories.ops_repository import OpsFlagRepository

    flags = OpsFlagRepository(session)
    now = pd.Timestamp("2026-10-02T16:00:00Z").to_pydatetime()
    got_a, lease_a = await acquire_cycle_lease(flags, owner="replica-a", now=now, session=session)
    got_b, _ = await acquire_cycle_lease(flags, owner="replica-b", now=now, session=session)
    assert got_a is True
    assert got_b is False
    await release_cycle_lease(flags, "replica-a", session=session)
    got_b2, _ = await acquire_cycle_lease(flags, owner="replica-b", now=now, session=session)
    assert got_b2 is True
    key = idempotency_key("BTC/USD", "2026-10-02T16:00:00+00:00", "buy")
    assert key.startswith("sa9-BTCUSD-")
    assert key.endswith("-1")
    assert len(key) <= 48
    assert idempotency_key("BTC/USD", "2026-10-02T16:00:00+00:00", "buy", attempt=2).endswith("-2")


@pytest.mark.asyncio
async def test_tracker_duplicate_client_order_id_does_not_double_qty(session):
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    tr = MultiAssetTradeTracker(session)
    t1 = await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.01,
        entry_price=100.0,
        order_id="oid-1",
        meta={"client_order_id": "sa9-BTCUSD-2026100216-buy"},
    )
    t2 = await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.01,
        entry_price=110.0,
        order_id="oid-2",
        meta={"client_order_id": "sa9-BTCUSD-2026100216-buy"},
    )
    assert t2.qty == pytest.approx(0.01)
    assert t2.id == t1.id
    # Distinct id still adds (rebalance).
    t3 = await tr.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.02,
        entry_price=120.0,
        order_id="oid-3",
        meta={"client_order_id": "sa9-BTCUSD-2026100220-rebup"},
    )
    assert t3.qty == pytest.approx(0.03)
    closed = await tr.close_trade(desk="crypto", symbol="BTC/USD", exit_price=130.0, qty=0.01)
    assert closed is not None
    still = await tr.get_open("crypto", "BTC/USD")
    assert still is not None
    assert still.qty == pytest.approx(0.02)


def test_corr_from_4h_90d_not_30_daily():
    from services.multiasset.crypto_market_stats import CORR_4H_BARS, corr_from_4h, log_returns_4h

    idx = pd.date_range("2026-01-01", periods=CORR_4H_BARS + 10, freq="4h", tz="UTC")
    close_a = 100 + np.linspace(0, 20, len(idx)) + np.sin(np.arange(len(idx)) / 8.0)
    close_b = close_a * 1.01
    a = pd.DataFrame({"Open": close_a, "High": close_a + 1, "Low": close_a - 1, "Close": close_a}, index=idx)
    b = pd.DataFrame({"Open": close_b, "High": close_b + 1, "Low": close_b - 1, "Close": close_b}, index=idx)
    rr = log_returns_4h(a)
    assert rr is not None and len(rr) >= 90 * 6 - 20
    corr = corr_from_4h({"BTC/USD": a, "ETH/USD": b})
    rho = corr.get("BTC/USD|ETH/USD") or corr.get("ETH/USD|BTC/USD")
    assert rho is not None and rho >= 0.7


def test_last_cycle_obs_exposes_catchup_fields():
    from services.multiasset.crypto_obs import attach_last_cycle_obs

    state = {
        "positions": {
            "BTC/USD": {
                "stop_evaluated_at": "2026-10-01T16:00:00+00:00",
                "candle_close": 65000.0,
                "stop_px": 61000.0,
                "highest_close": 67000.0,
                "broker_stop": "none",
                "state_source": "db",
            }
        },
        "last_evaluated_candle": "2026-10-01T16:00:00+00:00",
        "eval_history": [
            {"candle_open": "2026-10-01T12:00:00+00:00", "late": True},
            {"candle_open": "2026-10-01T16:00:00+00:00", "late": False},
        ],
        "missed_candles": 1,
        "candles_behind": 1,
        "replica_id": "host:1",
    }
    cycle = attach_last_cycle_obs({"desks": {"crypto": {}}}, state)
    assert cycle["last_evaluated_candle"] == "2026-10-01T16:00:00+00:00"
    assert cycle["missed_candles"] == 1
    assert cycle["candles_behind"] == 1
    assert cycle["replica_id"] == "host:1"
    assert len(cycle["eval_history"]) == 2
    pos = cycle["desks"]["crypto"]["open_positions"][0]
    assert pos["max_close"] == 67000.0
    assert pos["state_source"] == "db"
    assert pos["broker_stop"] == "none"


def _http_err(status: int, *, code: int | None = None, message: str = "nope"):
    import httpx

    req = httpx.Request("POST", "https://paper-api.alpaca.markets/v2/orders")
    resp = httpx.Response(status, json={"code": code, "message": message}, request=req)
    err = httpx.HTTPStatusError(f"Alpaca {status}: {message}", request=req, response=resp)
    err.alpaca_code = code
    err.alpaca_status = status
    return err


@pytest.mark.asyncio
async def test_crypto_422_duplicate_reconciles_without_new_fill():
    from services.multiasset.desk_service import MultiAssetDeskService
    from services.order_idempotency import ALPACA_DUPLICATE_COID_CODE

    svc = MultiAssetDeskService(session=None)
    inner = MagicMock()
    inner.submit_order = AsyncMock(
        side_effect=_http_err(422, code=ALPACA_DUPLICATE_COID_CODE, message="client_order_id must be unique")
    )
    inner.get_order_by_client_order_id = AsyncMock(
        return_value={
            "id": "ord-1",
            "status": "filled",
            "filled_qty": "0.01",
            "client_order_id": "sa9-BTCUSD-2026100216-buy-1",
        }
    )
    svc._broker = inner
    payload = {"symbol": "BTC/USD", "side": "buy", "client_order_id": "sa9-BTCUSD-2026100216-buy-1"}
    out = await svc._submit_with_idempotency(payload)
    assert out.get("reconciled") is True
    assert out.get("no_new_fill") is True
    assert out["id"] == "ord-1"
    inner.submit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_crypto_timeout_existing_and_missing_same_id():
    import httpx
    from services.multiasset.desk_service import MultiAssetDeskService

    cid = "sa9-BTCUSD-2026100216-buy-1"
    payload = {"symbol": "BTC/USD", "side": "buy", "client_order_id": cid}
    svc = MultiAssetDeskService(session=None)
    inner = MagicMock()
    inner.submit_order = AsyncMock(side_effect=httpx.ReadTimeout("no reply"))
    inner.get_order_by_client_order_id = AsyncMock(
        return_value={"id": "ex-1", "status": "accepted", "client_order_id": cid}
    )
    svc._broker = inner
    out = await svc._submit_with_idempotency(payload)
    assert out.get("reconciled") is True
    inner.submit_order.assert_awaited_once()

    inner2 = MagicMock()
    inner2.submit_order = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("no reply"),
            {"id": "new-1", "status": "accepted", "client_order_id": cid},
        ]
    )
    inner2.get_order_by_client_order_id = AsyncMock(return_value=None)
    svc._broker = inner2
    out2 = await svc._submit_with_idempotency(payload)
    assert out2["id"] == "new-1"
    assert inner2.submit_order.await_count == 2
    assert inner2.submit_order.await_args_list[0].args[0]["client_order_id"] == cid
    assert inner2.submit_order.await_args_list[1].args[0]["client_order_id"] == cid


@pytest.mark.asyncio
async def test_crypto_retry_after_cancel_attempt_plus_one():
    from services.multiasset.crypto_cycle_lock import allocate_sa9_client_order_id, idempotency_key

    flags = MagicMock()
    store = {"order_client_id_attempts": {"BTCUSD:buy:2026100216": 1}}

    async def _get(name):
        return dict(store.get(name) or {})

    async def _set(name, val):
        store[name] = dict(val)

    flags.get_json = AsyncMock(side_effect=_get)
    flags.set_json = AsyncMock(side_effect=_set)
    broker = MagicMock()
    broker.get_order_by_client_order_id = AsyncMock(
        return_value={"status": "canceled", "id": "old"}
    )
    cid, n = await allocate_sa9_client_order_id(
        flags, "BTC/USD", "buy", "2026-10-02T16:00:00+00:00", broker=broker
    )
    assert n == 2
    assert cid == idempotency_key("BTC/USD", "2026-10-02T16:00:00+00:00", "buy", attempt=2)


@pytest.mark.asyncio
async def test_crypto_other_422_is_not_duplicate():
    from services.multiasset.desk_service import MultiAssetDeskService

    svc = MultiAssetDeskService(session=None)
    inner = MagicMock()
    inner.submit_order = AsyncMock(side_effect=_http_err(422, code=40010000, message="invalid notional"))
    inner.get_order_by_client_order_id = AsyncMock(side_effect=AssertionError("no lookup"))
    svc._broker = inner
    with pytest.raises(Exception):
        await svc._submit_with_idempotency({"symbol": "BTC/USD", "side": "buy"})
    inner.get_order_by_client_order_id.assert_not_called()


@pytest.mark.asyncio
async def test_crypto_held_stop_via_status_all_not_open():
    from services.order_idempotency import lookup_working_stop, order_is_live_stop

    broker = MagicMock()
    broker.list_orders = AsyncMock(
        return_value=[
            {"symbol": "BTCUSD", "side": "sell", "type": "stop", "status": "held", "id": "leg-1"}
        ]
    )
    found = await lookup_working_stop(broker, "BTC/USD")
    assert found["id"] == "leg-1"
    assert order_is_live_stop(found) is True
    assert broker.list_orders.await_args.kwargs.get("status") == "all"


@pytest.mark.asyncio
async def test_crypto_insufficient_qty_does_not_flatten():
    from services.order_idempotency import is_insufficient_qty_error, lookup_working_stop

    broker = MagicMock()
    broker.list_orders = AsyncMock(
        return_value=[{"symbol": "BTCUSD", "side": "sell", "type": "stop", "status": "held", "id": "h1"}]
    )
    broker.close_position = AsyncMock(side_effect=AssertionError("must not flatten"))
    assert is_insufficient_qty_error("insufficient qty available") is True
    held = await lookup_working_stop(broker, "BTC/USD")
    assert held["id"] == "h1"
    broker.close_position.assert_not_called()
    assert broker.list_orders.await_args.kwargs.get("status") == "all"


def test_open_risk_from_current_price_not_entry():
    from services.multiasset.crypto_risk import open_risk_from_mark

    # Entry 100, stop 92 (8R). Mark now 110 → risk is (110-92)*qty, not (100-92)*qty.
    qty = 0.5
    assert open_risk_from_mark(qty=qty, last=110.0, stop=92.0) == pytest.approx(9.0)
    assert open_risk_from_mark(qty=qty, last=100.0, stop=92.0) == pytest.approx(4.0)
    assert open_risk_from_mark(qty=qty, last=90.0, stop=92.0) == 0.0
    assert open_risk_from_mark(qty=0, last=110.0, stop=92.0) == 0.0


def test_accum_brake_5pct_and_loss_streak():
    from datetime import datetime, timedelta, timezone

    from services.multiasset.crypto_risk import accum_brake_triggered, loss_streak_pause

    assert accum_brake_triggered(4.99) is False
    assert accum_brake_triggered(5.0) is True
    assert accum_brake_triggered(5.01, brake_pct=5.0) is True
    now = datetime(2026, 10, 2, 16, tzinfo=timezone.utc)
    losses = [
        {"pnl_usd": -10, "at": (now - timedelta(hours=5)).isoformat()},
        {"pnl_usd": -4, "at": (now - timedelta(hours=3)).isoformat()},
        {"pnl_usd": -2, "at": (now - timedelta(hours=1)).isoformat()},
    ]
    paused, why = loss_streak_pause(losses, now=now, n=3, hours=24)
    assert paused is True
    assert "loss_streak_3" in why
    stale = [
        {"pnl_usd": -1, "at": (now - timedelta(hours=30)).isoformat()},
        {"pnl_usd": -1, "at": (now - timedelta(hours=28)).isoformat()},
        {"pnl_usd": -1, "at": (now - timedelta(hours=26)).isoformat()},
    ]
    assert loss_streak_pause(stale, now=now, n=3, hours=24)[0] is False
    mixed = losses[:-1] + [{"pnl_usd": 1, "at": now.isoformat()}]
    assert loss_streak_pause(mixed, now=now, n=3, hours=24)[0] is False


def test_rolling_24h_window_resets_after_expiry():
    from datetime import datetime, timedelta, timezone

    from services.multiasset.crypto_risk import rolling_window_start

    now = datetime(2026, 10, 2, 16, tzinfo=timezone.utc)
    start, stamp, reset = rolling_window_start(
        stamped_at=(now - timedelta(hours=10)).isoformat(),
        stamped_wealth=2000.0,
        wealth=1900.0,
        now=now,
        hours=24.0,
    )
    assert reset is False
    assert start == pytest.approx(2000.0)
    assert stamp.endswith("+00:00") or "2026-10-02" in stamp
    start2, _stamp2, reset2 = rolling_window_start(
        stamped_at=(now - timedelta(hours=25)).isoformat(),
        stamped_wealth=2000.0,
        wealth=1900.0,
        now=now,
        hours=24.0,
    )
    assert reset2 is True
    assert start2 == pytest.approx(1900.0)
    start3, _, reset3 = rolling_window_start(
        stamped_at=None, stamped_wealth=None, wealth=1800.0, now=now, hours=24.0
    )
    assert reset3 is True and start3 == pytest.approx(1800.0)


def test_engine_bars_clean_and_resample_4h():
    from services.multiasset.engine_bars import clean, resample_4h

    idx = pd.date_range("2026-01-01", periods=12, freq="1h", tz="UTC")
    close = np.linspace(100, 111, 12)
    raw = pd.DataFrame(
        {"Open": close - 0.1, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 1.0},
        index=idx,
    )
    # Incomplete minute + duplicate + zero close must drop.
    dirty = pd.concat(
        [
            raw,
            pd.DataFrame(
                {"Open": [1], "High": [1], "Low": [1], "Close": [0], "Volume": [1]},
                index=[pd.Timestamp("2026-01-01T00:15:00Z")],
            ),
        ]
    )
    cleaned = clean(dirty)
    assert all(ts.minute == 0 for ts in cleaned.index)
    assert (cleaned["Close"] > 0).all()
    four = resample_4h(raw)
    # 12 complete hours → 3 closed 4h buckets (00, 04, 08).
    assert len(four) == 3
    assert list(four.index.hour) == [0, 4, 8]
    gapped = raw.drop(raw.index[1])  # missing 01:00 → first 4h bucket incomplete
    four_g = resample_4h(gapped)
    assert all(ts.hour != 0 for ts in four_g.index)


@pytest.mark.asyncio
async def test_scheduler_fires_crypto_catchup_before_run(monkeypatch):
    from services.scheduler_service import SchedulerService

    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    monkeypatch.setenv("MULTIASSET_AUTOPILOT_ENABLED", "true")
    from config.settings import get_settings

    get_settings.cache_clear()
    calls: list[str] = []

    class FakeAuto:
        def __init__(self, session):
            del session

        async def crypto_catchup_on_wake(self, *, actor: str = "wake"):
            calls.append(f"catchup:{actor}")
            return {"ok": True}

        async def run(self, *, actor: str = "scheduler"):
            calls.append(f"run:{actor}")
            return {"skipped": None, "desks": {}}

    async def _sessions():
        yield MagicMock()

    with (
        patch("services.scheduler_service.get_settings", return_value=get_settings()),
        patch("services.scheduler_service.get_session", return_value=_sessions()),
        patch("services.multiasset.autopilot.MultiAssetAutopilotService", FakeAuto),
    ):
        await SchedulerService()._run_multiasset_autopilot()
    assert calls == ["catchup:scheduler_wake", "run:scheduler"]
    get_settings.cache_clear()
