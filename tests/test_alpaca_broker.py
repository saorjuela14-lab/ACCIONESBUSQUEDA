"""Tests for Alpaca Trading API integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from domain.broker import BrokerOrderRequest, ExecuteLine, ExecuteOrdersRequest
from providers.broker.alpaca_provider import PAPER_BASE_URL, AlpacaBrokerProvider
from services.alpaca_order_service import AlpacaOrderService


def _mock_response(status: int, json_data, headers=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.content = b"{}" if json_data is not None else b""
    resp.text = str(json_data)
    resp.headers = headers or {"X-Request-ID": "req-test-123"}
    resp.json = MagicMock(return_value=json_data)
    resp.request = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_alpaca_not_configured():
    broker = AlpacaBrokerProvider(api_key="", secret_key="", paper=True)
    assert not broker.is_configured()
    assert broker.base_url == PAPER_BASE_URL
    with pytest.raises(ValueError, match="Alpaca no configurada"):
        await broker.get_account()


@pytest.mark.asyncio
async def test_alpaca_get_account_captures_request_id():
    broker = AlpacaBrokerProvider(api_key="key", secret_key="sec", paper=True)
    account = {
        "id": "acc-1",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "100000",
        "buying_power": "200000",
        "portfolio_value": "100000",
        "equity": "100000",
    }
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=_mock_response(200, account))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.broker.alpaca_provider.httpx.AsyncClient", return_value=mock_client):
        data = await broker.get_account()

    assert data["cash"] == "100000"
    assert data["_request_id"] == "req-test-123"
    assert broker.last_request_id == "req-test-123"
    call_kwargs = mock_client.request.call_args
    assert call_kwargs[0][0] == "GET"
    assert "/v2/account" in call_kwargs[0][1]
    headers = call_kwargs[1]["headers"]
    assert headers["APCA-API-KEY-ID"] == "key"
    assert headers["APCA-API-SECRET-KEY"] == "sec"


@pytest.mark.asyncio
async def test_alpaca_submit_order_payload():
    broker = AlpacaBrokerProvider(api_key="key", secret_key="sec", paper=True)
    order_resp = {
        "id": "ord-1",
        "symbol": "AAPL",
        "qty": "1",
        "side": "buy",
        "type": "market",
        "status": "accepted",
        "client_order_id": "c1",
    }
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=_mock_response(200, order_resp))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.broker.alpaca_provider.httpx.AsyncClient", return_value=mock_client):
        data = await broker.submit_order({
            "symbol": "AAPL",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        })

    assert data["id"] == "ord-1"
    body = mock_client.request.call_args[1]["json"]
    assert body["symbol"] == "AAPL"
    assert body["side"] == "buy"


@pytest.mark.asyncio
async def test_order_service_status_unconfigured():
    broker = AlpacaBrokerProvider(api_key="", secret_key="")
    svc = AlpacaOrderService(broker=broker)
    st = await svc.status()
    assert st.configured is False
    assert st.connected is False
    assert "ALPACA_API_KEY" in st.message


@pytest.mark.asyncio
async def test_order_service_live_requires_confirm():
    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=False)
    svc = AlpacaOrderService(broker=broker)
    result = await svc.execute(ExecuteOrdersRequest(
        lines=[ExecuteLine(ticker="AAPL", shares=1)],
        confirm_live=False,
    ))
    assert not result.submitted
    assert any("confirm_live" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_order_service_dry_run_builds_bracket():
    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=True)
    svc = AlpacaOrderService(broker=broker)
    result = await svc.execute(ExecuteOrdersRequest(
        lines=[ExecuteLine(
            ticker="SNDL",
            shares=10,
            stop_loss=1.5,
            take_profit=2.2,
        )],
        dry_run=True,
    ))
    assert len(result.submitted) == 1
    assert result.submitted[0].status == "dry_run"
    payload = result.submitted[0].raw["payload"]
    assert payload["order_class"] == "bracket"
    assert payload["take_profit"]["limit_price"] == "2.2"
    assert payload["stop_loss"]["stop_price"] == "1.5"
    assert payload["qty"] == "10"
    assert payload["client_order_id"].startswith("nexbuy-")


@pytest.mark.asyncio
async def test_effective_live_trade_env():
    from config.settings import Settings

    s = Settings(alpaca_paper=True, alpaca_live_trade=True)
    assert s.effective_alpaca_paper is False
    s2 = Settings(alpaca_paper=False, alpaca_live_trade=False)
    assert s2.effective_alpaca_paper is True
    s3 = Settings(alpaca_paper=False, alpaca_live_trade=None)
    assert s3.effective_alpaca_paper is False


@pytest.mark.asyncio
async def test_cancel_all_and_clock_provider():
    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=False)
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(
        side_effect=[
            _mock_response(200, {"is_open": True, "next_open": "2024-01-02T14:30:00Z", "next_close": "2024-01-02T21:00:00Z", "timestamp": "2024-01-02T15:00:00Z"}),
            _mock_response(200, [{"id": "o1", "status": 200}]),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.broker.alpaca_provider.httpx.AsyncClient", return_value=mock_client):
        clock = await broker.get_clock()
        cancelled = await broker.cancel_all_orders()

    assert clock["is_open"] is True
    assert isinstance(cancelled, list)
    assert mock_client.request.call_args_list[1][0][0] == "DELETE"
    assert mock_client.request.call_args_list[1][0][1].endswith("/v2/orders")


@pytest.mark.asyncio
async def test_doctor_unconfigured():
    broker = AlpacaBrokerProvider(api_key="", secret_key="")
    report = await AlpacaOrderService(broker=broker).doctor()
    assert report.ok is False
    assert report.configured is False


@pytest.mark.asyncio
async def test_order_service_submit_one_maps_result():
    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=True)
    broker.submit_order = AsyncMock(return_value={
        "id": "o1",
        "symbol": "F",
        "qty": "5",
        "filled_qty": "0",
        "side": "buy",
        "type": "market",
        "status": "accepted",
        "_request_id": "rid-9",
    })
    svc = AlpacaOrderService(broker=broker)
    result = await svc.submit_one(BrokerOrderRequest(symbol="F", qty=5))
    assert result.id == "o1"
    assert result.symbol == "F"
    assert result.request_id == "rid-9"
    assert result.status == "accepted"


@pytest.mark.asyncio
async def test_lines_from_micro_plan():
    svc = AlpacaOrderService(broker=AlpacaBrokerProvider(api_key="k", secret_key="s"))
    lines = svc.lines_from_micro_plan([
        {"ticker": "abt", "shares": 3, "stop_loss": 1.0, "take_profit": 1.5},
        {"ticker": "SKIP", "shares": 0},
    ])
    assert len(lines) == 1
    assert lines[0].ticker == "ABT"
    assert lines[0].shares == 3.0


@pytest.mark.asyncio
async def test_http_error_includes_request_id():
    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=True)
    resp = _mock_response(403, {"message": "forbidden"}, headers={"X-Request-ID": "bad-id"})
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.broker.alpaca_provider.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError, match="X-Request-ID: bad-id"):
            await broker.get_account()


@pytest.mark.asyncio
async def test_alpaca_live_base_url_default():
    from providers.broker.alpaca_provider import LIVE_BASE_URL

    broker = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=False)
    assert broker.paper is False
    assert broker.base_url == LIVE_BASE_URL


@pytest.mark.asyncio
async def test_alpaca_market_data_quote_and_history():
    from providers.market.alpaca_provider import AlpacaMarketDataProvider

    provider = AlpacaMarketDataProvider(api_key="k", secret_key="s", feed="iex")

    trade_payload = {
        "symbol": "AAPL",
        "trade": {"p": 190.5, "s": 100, "t": "2024-01-02T15:00:00Z"},
        "currency": "USD",
    }
    bars_payload = {
        "symbol": "AAPL",
        "bars": [
            {
                "t": "2024-01-02T05:00:00Z",
                "o": 189,
                "h": 191,
                "l": 188,
                "c": 190.5,
                "v": 1000,
                "n": 10,
                "vw": 190,
            }
        ],
        "next_page_token": None,
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_response(200, trade_payload),
            _mock_response(200, bars_payload),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.market.alpaca_provider.httpx.AsyncClient", return_value=mock_client):
        quote = await provider.get_quote("AAPL")
        hist = await provider.get_history("AAPL", period="5d", interval="1d")

    assert quote["current_price"] == 190.5
    assert quote["source"] == "alpaca"
    assert not hist.empty
    assert float(hist["Close"].iloc[-1]) == 190.5
    assert provider.last_request_id == "req-test-123"


@pytest.mark.asyncio
async def test_composite_prefers_alpaca():
    import pandas as pd
    from providers.market.composite_market_provider import CompositeMarketDataProvider

    sample_df = pd.DataFrame(
        {"Open": [100], "High": [101], "Low": [99], "Close": [100.5], "Volume": [1000]},
        index=pd.to_datetime(["2025-01-01"]),
    )
    sample_quote = {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "current_price": 150.0,
        "sector": "Technology",
        "source": "alpaca",
    }

    alpaca = AsyncMock()
    alpaca.get_history.return_value = sample_df
    alpaca.get_quote.return_value = sample_quote

    polygon = AsyncMock()
    alpha = AsyncMock()
    yfinance = AsyncMock()

    provider = CompositeMarketDataProvider(
        alpaca=alpaca, polygon=polygon, alpha_vantage=alpha, yfinance=yfinance
    )
    df = await provider.get_history("AAPL")
    quote = await provider.get_quote("AAPL")
    assert not df.empty
    assert quote["source"] == "alpaca"
    polygon.get_history.assert_not_called()
    alpha.get_history.assert_not_called()
