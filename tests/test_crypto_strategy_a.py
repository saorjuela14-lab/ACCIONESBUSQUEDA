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
    btc_above_daily_sma200,
    stop_is_tradable,
    strategy_a_signal,
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


def _ohlc_uptrend(n: int = 500, start: float = 100.0, end: float = 200.0) -> pd.DataFrame:
    close = np.linspace(start, end, n)
    high = close + 0.4
    low = close - 0.4
    open_ = close - 0.1
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close})


def test_strategy_a_buy_on_donchian_breakout():
    df = _ohlc_uptrend(520)
    prior_high = float(df["High"].iloc[-31:-1].max())
    df.loc[df.index[-1], "Close"] = prior_high + 1.0
    df.loc[df.index[-1], "High"] = prior_high + 1.2
    btc = _ohlc_uptrend(250, 40000, 50000)
    assert btc_above_daily_sma200(btc)[0] is True
    sig = strategy_a_signal(df, btc_daily=btc)
    assert sig.side == "buy"
    assert sig.stop_px is not None
    assert stop_is_tradable(float(df["Close"].iloc[-1]), sig.stop_px)


def test_strategy_a_blocks_when_btc_below_sma200():
    df = _ohlc_uptrend(520)
    btc = _ohlc_uptrend(250, 50000, 40000)
    sig = strategy_a_signal(df, btc_daily=btc)
    assert sig.side == "hold"
    assert "sma200" in sig.reason


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
        symbol="BTC/USD", equity=eq, entry=100.0, stop=99.0, book=book2, n_trades=20
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
