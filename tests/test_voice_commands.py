"""Tests for voice command parsing and execution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import voice_command_service as vcs
from services.voice_command_service import VoiceCommandService


@pytest.fixture
def voice_svc():
    return VoiceCommandService()


@pytest.fixture(autouse=True)
def clear_pending():
    vcs._PENDING.clear()
    yield
    vcs._PENDING.clear()


def test_parse_market_intent(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("como esta el mercado hoy"),
        "como esta el mercado hoy",
    )
    assert intent == "market"
    assert params == {}


def test_parse_analyze_ticker(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("analiza nvda"),
        "analiza nvda",
    )
    assert intent == "analyze"
    assert params["ticker"] == "NVDA"


def test_parse_analyze_alias(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("analiza apple"),
        "analiza apple",
    )
    assert intent == "analyze"
    assert params["ticker"] == "AAPL"


def test_parse_quote(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("precio de nvidia"),
        "precio de nvidia",
    )
    assert intent == "quote"
    assert params["ticker"] == "NVDA"


def test_parse_technical(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("tecnico de tsla"),
        "tecnico de tsla",
    )
    assert intent == "technical"
    assert params["ticker"] == "TSLA"


def test_parse_buy(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("compra 2 aapl"),
        "compra 2 aapl",
    )
    assert intent == "buy"
    assert params["ticker"] == "AAPL"
    assert int(params["shares"]) == 2


def test_parse_sell(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("vende tesla"),
        "vende tesla",
    )
    assert intent == "sell"
    assert params["ticker"] == "TSLA"


def test_parse_confirm(voice_svc):
    intent, _ = voice_svc._parse_intent(voice_svc._normalize("confirma"), "confirma")
    assert intent == "confirm"


def test_parse_watchlist_add(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("agrega rklb a watchlist"),
        "agrega rklb a watchlist",
    )
    assert intent == "watchlist_add"
    assert params["ticker"] == "RKLB"


def test_parse_discovery(voice_svc):
    intent, params = voice_svc._parse_intent(
        voice_svc._normalize("descubre biotech"),
        "descubre biotech",
    )
    assert intent == "discovery"
    assert params["theme"] == "biotech"


@pytest.mark.asyncio
async def test_handle_unknown():
    svc = VoiceCommandService()
    session = AsyncMock()
    result = await svc.handle("xyz random phrase", session)
    assert result.intent == "unknown"
    assert not result.success


@pytest.mark.asyncio
async def test_handle_analyze_returns_ui_action():
    svc = VoiceCommandService()
    session = AsyncMock()
    with patch("services.voice_command_service.get_market_provider") as gp:
        market = MagicMock()
        market.get_quote = AsyncMock(return_value={"current_price": 100.0})
        gp.return_value = market
        result = await svc.handle("analiza VRT", session)
    assert result.intent == "analyze"
    assert result.ui_action == "analyze:VRT"
    assert "VRT" in result.speech


@pytest.mark.asyncio
async def test_handle_help():
    svc = VoiceCommandService()
    result = await svc.handle("ayuda", AsyncMock())
    assert result.intent == "help"
    assert result.success


@pytest.mark.asyncio
async def test_buy_preview_sets_pending():
    svc = VoiceCommandService()
    session = AsyncMock()

    market = MagicMock()
    market.get_quote = AsyncMock(return_value={"current_price": 50.0, "company_name": "Apple"})

    alpaca = MagicMock()
    alpaca.paper = True
    alpaca.is_configured = MagicMock(return_value=True)
    status = MagicMock()
    status.configured = True
    status.connected = True
    status.paper = True
    status.market_open = True
    status.account = MagicMock(buying_power=1000.0, cash=1000.0, equity=1000.0)
    alpaca.status = AsyncMock(return_value=status)
    preview = MagicMock()
    preview.warnings = []
    preview.submitted = []
    preview.failed = []
    preview.model_dump = MagicMock(return_value={})
    alpaca.execute = AsyncMock(return_value=preview)

    with patch("services.voice_command_service.get_market_provider", return_value=market), \
         patch("services.voice_command_service.AlpacaOrderService", return_value=alpaca):
        result = await svc.handle("compra 1 AAPL", session, portfolio_id="p1")

    assert result.intent == "buy"
    assert result.requires_confirmation is True
    assert vcs._get_pending("p1") is not None
    assert "confirma" in result.speech.lower()


@pytest.mark.asyncio
async def test_confirm_without_pending():
    svc = VoiceCommandService()
    result = await svc.handle("confirma", AsyncMock())
    assert result.intent == "confirm"
    assert not result.success
