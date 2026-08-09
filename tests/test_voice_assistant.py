"""Tests for conversational voice assistant (Viernes)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.voice_assistant_service import VoiceAssistantService
from services.voice_change_service import VoiceChangeService


@pytest.mark.asyncio
async def test_chat_trade_command_fast_path():
    svc = VoiceAssistantService()
    db = MagicMock()
    fake = MagicMock()
    fake.success = True
    fake.speech = "Orden lista. Di confirma."
    fake.ui_action = None
    fake.requires_confirmation = True
    fake.pending_action = {"side": "buy"}
    fake.intent = "buy"
    fake.data = None

    with patch.object(svc._commands, "handle", new=AsyncMock(return_value=fake)):
        result = await svc.chat("compra 1 AAPL", db, session_id="t1")
    assert result.mode == "command"
    assert result.requires_confirmation is True
    assert "jefe" in result.speech.lower() or "Orden" in result.speech


@pytest.mark.asyncio
async def test_chat_fallback_without_openai():
    svc = VoiceAssistantService()
    db = MagicMock()
    fake = MagicMock()
    fake.success = False
    fake.speech = "No entendí"
    fake.ui_action = None
    fake.requires_confirmation = False
    fake.pending_action = None
    fake.intent = "unknown"
    fake.data = None

    with patch("services.voice_assistant_service.get_settings") as gs, \
         patch.object(svc._commands, "handle", new=AsyncMock(return_value=fake)):
        s = MagicMock()
        s.openai_api_key = ""
        s.voice_chat_enabled = True
        s.voice_assistant_name = "Viernes"
        s.voice_boss_title = "jefe"
        gs.return_value = s
        result = await svc.chat("rediseña la estrategia del homepage", db, session_id="t2")
    assert result.mode == "fallback"
    assert "OPENAI" in result.speech.upper() or "openai" in result.speech.lower()


@pytest.mark.asyncio
async def test_queue_product_change_local(tmp_path, monkeypatch):
    store = tmp_path / "voice_change_requests.json"
    monkeypatch.setattr("services.voice_change_service._STORE", store)
    with patch("services.voice_change_service.get_settings") as gs:
        s = MagicMock()
        s.github_token = ""
        s.github_repo = "saorjuela14-lab/ACCIONESBUSQUEDA"
        gs.return_value = s
        item = await VoiceChangeService().queue_change(
            title="Cambiar hero",
            description="Quiero el hero full-bleed",
            area="ui",
        )
    assert item["id"]
    assert item["status"] == "queued"
    listed = VoiceChangeService().list_recent()
    assert listed[0]["title"] == "Cambiar hero"


def test_assistant_status_shape():
    with patch("services.voice_assistant_service.get_settings") as gs, patch(
        "services.cursor_agent_service.get_settings"
    ) as gsc:
        s = MagicMock()
        s.voice_chat_enabled = True
        s.openai_api_key = "sk-test"
        s.voice_assistant_name = "Viernes"
        s.voice_boss_title = "jefe"
        s.openai_model = "gpt-4o-mini"
        s.github_token = ""
        s.github_repo = "x/y"
        s.cursor_api_key = "crsr_test"
        s.cursor_agent_enabled = True
        s.cursor_agent_repo_url = "https://github.com/saorjuela14-lab/ACCIONESBUSQUEDA"
        s.cursor_agent_starting_ref = "main"
        s.cursor_agent_auto_create_pr = True
        s.cursor_agent_model = ""
        gs.return_value = s
        gsc.return_value = s
        st = VoiceAssistantService().status()
    assert st["assistant_name"] == "Viernes"
    assert st["openai_configured"] is True
    assert st["cursor_configured"] is True
