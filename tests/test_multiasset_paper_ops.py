"""Paper-only Multi-Asset activities + stale market-buy cancel."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from services.multiasset.paper_broker import MultiAssetNotPaperError
from services.multiasset.paper_ops import (
    cancel_stale_paper_market_buys,
    is_stale_market_buy,
    list_paper_activities,
    normalize_activity_symbol,
    sanitize_activity,
)
from utils.metrics import metrics


NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def _order(**kwargs):
    base = {
        "id": "ord-1",
        "symbol": "WIF/USD",
        "side": "buy",
        "type": "market",
        "status": "new",
        "time_in_force": "gtc",
        "created_at": (NOW - timedelta(days=45)).isoformat(),
    }
    base.update(kwargs)
    return base


def test_stale_filter_only_old_market_buys():
    assert is_stale_market_buy(_order(), now=NOW) is True
    assert is_stale_market_buy(_order(status="accepted"), now=NOW) is True
    assert is_stale_market_buy(_order(created_at=(NOW - timedelta(hours=2)).isoformat()), now=NOW) is False
    assert is_stale_market_buy(_order(type="stop", side="sell", status="new"), now=NOW) is False
    assert is_stale_market_buy(_order(type="limit", side="sell"), now=NOW) is False
    assert is_stale_market_buy(_order(side="sell", type="market"), now=NOW) is False
    assert is_stale_market_buy(_order(type="stop"), now=NOW) is False
    assert is_stale_market_buy(_order(status="filled"), now=NOW) is False


def test_sanitize_activity_drops_secrets():
    row = {
        "id": "act-1",
        "activity_type": "CFEE",
        "symbol": "WIF/USD",
        "net_amount": "-0.12",
        "api_key": "PK-SECRET",
        "secret_key": "nope",
        "authorization": "Bearer x",
    }
    clean = sanitize_activity(row)
    assert clean["activity_type"] == "CFEE"
    assert "api_key" not in clean
    assert "secret_key" not in clean
    assert "authorization" not in clean


def test_normalize_activity_symbol_btcusd():
    assert normalize_activity_symbol("BTCUSD") == "BTC/USD"
    assert normalize_activity_symbol("btc/usd") == "BTC/USD"
    assert normalize_activity_symbol("ETHUSD") == "ETH/USD"
    assert sanitize_activity({"id": "1", "symbol": "BTCUSD"})["symbol"] == "BTC/USD"


@pytest.mark.asyncio
async def test_cancel_stale_market_buys_on_paper():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    stale = _order(id="wif-old", symbol="WIF/USD")
    fresh = _order(id="fresh", created_at=NOW.isoformat())
    stop = _order(id="stop-1", type="stop", side="sell", symbol="WIF/USD")
    limit_exit = _order(id="tp-1", type="limit", side="sell", symbol="LDO/USD")
    broker.list_orders = AsyncMock(return_value=[stale, fresh, stop, limit_exit])
    broker.cancel_order = AsyncMock(return_value={"ok": True})

    before = metrics.snapshot()["counters"].get("multiasset_stale_market_buys_cancelled", 0)
    out = await cancel_stale_paper_market_buys(broker, now=NOW)
    assert out["skipped"] is None
    assert out["cancelled"] == ["wif-old"]
    broker.cancel_order.assert_awaited_once_with("wif-old")
    after = metrics.snapshot()["counters"].get("multiasset_stale_market_buys_cancelled", 0)
    assert after == before + 1


@pytest.mark.asyncio
async def test_cancel_does_nothing_on_live_url():
    broker = MagicMock()
    broker.base_url = "https://api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.list_orders = AsyncMock()
    broker.cancel_order = AsyncMock()
    broker.get_account = AsyncMock()

    out = await cancel_stale_paper_market_buys(broker, now=NOW)
    assert out["skipped"] == "not_paper_url"
    assert out["cancelled"] == []
    broker.list_orders.assert_not_awaited()
    broker.cancel_order.assert_not_awaited()
    broker.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_wif_ldo_render_aug17_not_stops():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.list_orders = AsyncMock(
        return_value=[
            _order(id="wif-old", symbol="WIF/USD", created_at="2026-08-17T14:22:00Z"),
            _order(id="ldo-old", symbol="LDO/USD", created_at="2026-08-17T14:22:00Z"),
            _order(id="render-old", symbol="RENDER/USD", created_at="2026-08-17T14:22:00Z"),
            _order(id="stop-wif", symbol="WIF/USD", type="stop", side="sell", created_at="2026-08-17T14:22:00Z"),
            _order(id="tp-ldo", symbol="LDO/USD", type="limit", side="sell", created_at="2026-08-17T14:22:00Z"),
        ]
    )
    broker.cancel_order = AsyncMock(return_value={"ok": True})
    out = await cancel_stale_paper_market_buys(broker, now=NOW)
    assert set(out["cancelled"]) == {"wif-old", "ldo-old", "render-old"}
    cancelled_ids = [c.args[0] for c in broker.cancel_order.await_args_list]
    assert set(cancelled_ids) == {"wif-old", "ldo-old", "render-old"}


@pytest.mark.asyncio
async def test_cancel_does_nothing_when_paper_flag_false():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = False
    broker.is_configured.return_value = True
    broker.list_orders = AsyncMock()
    broker.cancel_order = AsyncMock()
    broker.get_account = AsyncMock()

    out = await cancel_stale_paper_market_buys(broker, now=NOW)
    assert out["skipped"] == "broker_paper_false"
    assert out["cancelled"] == []
    broker.list_orders.assert_not_awaited()
    broker.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_does_nothing_when_account_not_paper():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": False})
    broker.list_orders = AsyncMock()
    broker.cancel_order = AsyncMock()

    out = await cancel_stale_paper_market_buys(broker, now=NOW)
    assert out["skipped"] == "not_paper"
    broker.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_paper_activities_ok():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    broker.list_account_activities = AsyncMock(
        return_value=[
            {"id": "f1", "activity_type": "FILL", "symbol": "BONK/USD", "qty": "100", "price": "0.02"},
            {"id": "c1", "activity_type": "CFEE", "symbol": "BONK/USD", "net_amount": "-0.05", "api_key": "x"},
        ]
    )
    out = await list_paper_activities(broker, types="FILL,CFEE", after="2026-08-01")
    assert out["paper"] is True
    assert out["count"] == 2
    assert out["truncated"] is False
    assert out["next_page_token"] is None
    assert out["items"][1]["activity_type"] == "CFEE"
    assert "api_key" not in out["items"][1]
    broker.list_account_activities.assert_awaited_with(
        activity_types="FILL,CFEE",
        after="2026-08-01",
        until=None,
        page_size=100,
        page_token=None,
        direction="desc",
    )


@pytest.mark.asyncio
async def test_list_paper_activities_refuses_live_url():
    broker = MagicMock()
    broker.base_url = "https://api.alpaca.markets"
    broker.paper = True
    broker.list_account_activities = AsyncMock()
    with pytest.raises(MultiAssetNotPaperError):
        await list_paper_activities(broker, types="FILL")
    broker.list_account_activities.assert_not_awaited()


@pytest.mark.asyncio
async def test_activities_endpoint_desk_auth_and_live_url_409(monkeypatch, tmp_path):
    from apis.app import create_app
    from config.settings import get_settings
    from database.engine import init_db

    db = tmp_path / "paper-ops.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    monkeypatch.setenv("ALPACA_BETA_BASE_URL", "https://api.alpaca.markets")
    get_settings.cache_clear()
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauth = await client.get("/api/v1/ops/multiasset/activities")
        assert unauth.status_code == 401
        login = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        assert login.status_code == 200
        bad_after = await client.get("/api/v1/ops/multiasset/activities?after=nope")
        assert bad_after.status_code == 400
        live = await client.get("/api/v1/ops/multiasset/activities?types=FILL,CFEE")
        assert live.status_code == 409
        assert "paper" in str(live.json().get("detail", "")).lower()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_activities_endpoint_returns_sanitized_rows(monkeypatch, tmp_path):
    from apis.app import create_app
    from config.settings import get_settings
    from database.engine import init_db

    db = tmp_path / "paper-ops2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    monkeypatch.setenv("ALPACA_BETA_BASE_URL", "https://paper-api.alpaca.markets")
    get_settings.cache_clear()
    await init_db()
    app = create_app()
    mock_broker = MagicMock()
    mock_broker.base_url = "https://paper-api.alpaca.markets"
    mock_broker.paper = True
    mock_broker.is_configured.return_value = True
    mock_broker.get_account = AsyncMock(return_value={"paper": True})
    mock_broker.list_account_activities = AsyncMock(
        return_value=[{"id": "f1", "activity_type": "FILL", "symbol": "LDO/USD", "secret_key": "nope"}]
    )
    with patch(
        "services.multiasset.paper_broker.get_beta_broker_provider",
        return_value=mock_broker,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
            r = await client.get("/api/v1/ops/multiasset/activities?types=FILL&after=2026-08-01")
    assert r.status_code == 200
    body = r.json()
    assert body["paper"] is True
    assert body["truncated"] is False
    assert body["items"][0]["id"] == "f1"
    assert "secret_key" not in body["items"][0]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_paper_activities_walks_page_tokens():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})

    page1 = [{"id": f"p1-{i}", "activity_type": "FILL", "symbol": "BTCUSD"} for i in range(2)]
    page2 = [{"id": f"p2-{i}", "activity_type": "FILL", "symbol": "ETH/USD"} for i in range(2)]
    page3 = [{"id": "p3-0", "activity_type": "CFEE", "symbol": "BTCUSD"}]
    broker.list_account_activities = AsyncMock(side_effect=[page1, page2, page3])

    out = await list_paper_activities(
        broker, types="FILL,CFEE", page_size=2, direction="asc", until="2026-10-01"
    )
    assert out["count"] == 5
    assert out["truncated"] is False
    assert out["next_page_token"] is None
    assert out["direction"] == "asc"
    assert out["until"] == "2026-10-01"
    assert {row["symbol"] for row in out["items"]} == {"BTC/USD", "ETH/USD"}
    assert broker.list_account_activities.await_count == 3
    assert broker.list_account_activities.await_args_list[1].kwargs["page_token"] == "p1-1"
    assert broker.list_account_activities.await_args_list[2].kwargs["page_token"] == "p2-1"


@pytest.mark.asyncio
async def test_list_paper_activities_truncated_exposes_next_token():
    broker = MagicMock()
    broker.base_url = "https://paper-api.alpaca.markets"
    broker.paper = True
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(return_value={"paper": True})
    full = [{"id": f"x{i}", "activity_type": "FILL", "symbol": "SOLUSD"} for i in range(2)]
    broker.list_account_activities = AsyncMock(return_value=full)

    out = await list_paper_activities(broker, page_size=2, max_pages=1)
    assert out["count"] == 2
    assert out["truncated"] is True
    assert out["next_page_token"] == "x1"
    assert out["items"][0]["symbol"] == "SOL/USD"
    broker.list_account_activities.assert_awaited_once()
