"""Tests for voice command parsing and execution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.voice_command_service import VoiceCommandService


@pytest.fixture
def voice_svc():
    return VoiceCommandService()


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
