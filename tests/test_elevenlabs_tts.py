"""Tests for ElevenLabs TTS service and voice TTS routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis.routes import voice as voice_routes
from services.elevenlabs_tts_service import ElevenLabsTTSError, ElevenLabsTTSService


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.elevenlabs_api_key = "test-key"
    s.elevenlabs_voice_id = "EXAVITQu4vr4xnSDxMaL"
    s.elevenlabs_model_id = "eleven_flash_v2_5"
    s.elevenlabs_output_format = "mp3_44100_128"
    s.elevenlabs_stability = 0.42
    s.elevenlabs_similarity = 0.78
    s.elevenlabs_style = 0.25
    return s


def test_status_not_configured():
    with patch("services.elevenlabs_tts_service.get_settings") as gs:
        s = MagicMock()
        s.elevenlabs_api_key = ""
        s.elevenlabs_voice_id = "EXAVITQu4vr4xnSDxMaL"
        s.elevenlabs_model_id = "eleven_flash_v2_5"
        gs.return_value = s
        status = ElevenLabsTTSService().status()
        assert status["configured"] is False
        assert status["fallback"] == "browser_speech_synthesis"


@pytest.mark.asyncio
async def test_synthesize_success(fake_settings):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"ID3fake-mp3"
    mock_resp.headers = {"content-type": "audio/mpeg"}
    mock_resp.text = ""

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("services.elevenlabs_tts_service.get_settings", return_value=fake_settings), \
         patch("services.elevenlabs_tts_service.httpx.AsyncClient", return_value=mock_client):
        audio, ctype = await ElevenLabsTTSService().synthesize("Hola, soy tu asistente.")
        assert audio == b"ID3fake-mp3"
        assert "mpeg" in ctype
        mock_client.post.assert_awaited_once()
        kwargs = mock_client.post.await_args.kwargs
        assert kwargs["headers"]["xi-api-key"] == "test-key"
        assert kwargs["json"]["text"] == "Hola, soy tu asistente."
        assert kwargs["json"]["model_id"] == "eleven_flash_v2_5"


@pytest.mark.asyncio
async def test_synthesize_http_error(fake_settings):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.content = b""
    mock_resp.headers = {}
    mock_resp.text = "invalid api key"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("services.elevenlabs_tts_service.get_settings", return_value=fake_settings), \
         patch("services.elevenlabs_tts_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ElevenLabsTTSError):
            await ElevenLabsTTSService().synthesize("hola")


def test_tts_routes():
    app = FastAPI()
    app.include_router(voice_routes.router, prefix="/api/v1")
    client = TestClient(app)

    with patch.object(ElevenLabsTTSService, "status", return_value={
        "configured": True,
        "provider": "elevenlabs",
        "voice_id": "EXAVITQu4vr4xnSDxMaL",
        "model_id": "eleven_flash_v2_5",
        "fallback": "browser_speech_synthesis",
    }):
        r = client.get("/api/v1/voice/tts/status")
        assert r.status_code == 200
        assert r.json()["configured"] is True

    with patch.object(ElevenLabsTTSService, "configured", return_value=False):
        r = client.post("/api/v1/voice/tts", json={"text": "hola"})
        assert r.status_code == 503

    with patch.object(ElevenLabsTTSService, "configured", return_value=True), \
         patch.object(
             ElevenLabsTTSService,
             "synthesize",
             new=AsyncMock(return_value=(b"\xff\xfbaudio", "audio/mpeg")),
         ):
        r = client.post("/api/v1/voice/tts", json={"text": "Mercado estable hoy."})
        assert r.status_code == 200
        assert r.content == b"\xff\xfbaudio"
        assert "audio" in r.headers.get("content-type", "")
