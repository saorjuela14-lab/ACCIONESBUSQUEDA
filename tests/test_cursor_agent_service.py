"""Cursor Cloud Agents bridge used by Viernes."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.cursor_agent_service import CursorAgentError, CursorAgentService


@pytest.fixture
def settings_cursor(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.cursor_agent_service._STORE",
        tmp_path / "cursor_agent_launches.json",
    )
    with patch("services.cursor_agent_service.get_settings") as gs:
        s = MagicMock()
        s.cursor_api_key = "test-cursor-key"
        s.cursor_agent_enabled = True
        s.cursor_agent_repo_url = "https://github.com/saorjuela14-lab/ACCIONESBUSQUEDA"
        s.cursor_agent_starting_ref = "main"
        s.cursor_agent_auto_create_pr = True
        s.cursor_agent_model = ""
        gs.return_value = s
        yield s


@pytest.mark.asyncio
async def test_launch_agent_posts_v1(settings_cursor):
    svc = CursorAgentService()
    assert svc.configured() is True

    response = httpx.Response(
        200,
        json={
            "agent": {
                "id": "bc-aaaa",
                "name": "Fix deposit copy",
                "status": "ACTIVE",
                "url": "https://cursor.com/agents/bc-aaaa",
                "latestRunId": "run-bbbb",
            },
            "run": {"id": "run-bbbb", "status": "CREATING"},
        },
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=response)

    with patch("services.cursor_agent_service.httpx.AsyncClient", return_value=mock_client):
        out = await svc.launch(prompt="Quita el texto de Alpaca del depósito", title="Deposit UI")

    assert out["ok"] is True
    assert out["agent_id"] == "bc-aaaa"
    assert out["run_id"] == "run-bbbb"
    assert "cursor.com/agents/bc-aaaa" in out["url"]
    mock_client.post.assert_awaited_once()
    body = mock_client.post.await_args.kwargs["json"]
    assert body["repos"][0]["url"].endswith("ACCIONESBUSQUEDA")
    assert body["autoCreatePR"] is True
    assert "Pedido del jefe" in body["prompt"]["text"]
    assert svc.list_launches(1)[0]["agent_id"] == "bc-aaaa"


@pytest.mark.asyncio
async def test_launch_requires_key(monkeypatch, tmp_path):
    monkeypatch.setattr("services.cursor_agent_service._STORE", tmp_path / "x.json")
    with patch("services.cursor_agent_service.get_settings") as gs:
        s = MagicMock()
        s.cursor_api_key = ""
        s.cursor_agent_enabled = True
        gs.return_value = s
        with pytest.raises(CursorAgentError):
            await CursorAgentService().launch(prompt="haz un cambio grande")


@pytest.mark.asyncio
async def test_queue_change_launches_cursor(tmp_path, monkeypatch):
    from services.voice_change_service import VoiceChangeService

    store = tmp_path / "voice_change_requests.json"
    monkeypatch.setattr("services.voice_change_service._STORE", store)
    monkeypatch.setattr("services.cursor_agent_service._STORE", tmp_path / "launches.json")

    with patch("services.voice_change_service.get_settings") as gs, patch(
        "services.cursor_agent_service.get_settings"
    ) as gsc:
        s = MagicMock()
        s.github_token = ""
        s.github_repo = "saorjuela14-lab/ACCIONESBUSQUEDA"
        s.cursor_api_key = "k"
        s.cursor_agent_enabled = True
        s.cursor_agent_repo_url = "https://github.com/saorjuela14-lab/ACCIONESBUSQUEDA"
        s.cursor_agent_starting_ref = "main"
        s.cursor_agent_auto_create_pr = True
        s.cursor_agent_model = ""
        gs.return_value = s
        gsc.return_value = s

        response = httpx.Response(
            200,
            json={
                "agent": {
                    "id": "bc-1",
                    "url": "https://cursor.com/agents/bc-1",
                    "status": "ACTIVE",
                },
                "run": {"id": "run-1", "status": "CREATING"},
            },
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=response)

        with patch("services.cursor_agent_service.httpx.AsyncClient", return_value=mock_client):
            item = await VoiceChangeService().queue_change(
                title="Limpiar copy",
                description="Quitar mensajes repetitivos",
                area="ui",
            )

    assert item["status"] == "cursor_agent"
    assert item["cursor_agent_id"] == "bc-1"


@pytest.mark.asyncio
async def test_viernes_change_fast_path_without_openai(monkeypatch, tmp_path):
    from services.voice_assistant_service import VoiceAssistantService

    monkeypatch.setattr("services.cursor_agent_service._STORE", tmp_path / "launches.json")
    svc = VoiceAssistantService()
    db = MagicMock()

    with patch("services.voice_assistant_service.get_settings") as gs, patch(
        "services.cursor_agent_service.get_settings"
    ) as gsc:
        s = MagicMock()
        s.openai_api_key = ""
        s.voice_chat_enabled = True
        s.voice_assistant_name = "Viernes"
        s.voice_boss_title = "jefe"
        s.cursor_api_key = "k"
        s.cursor_agent_enabled = True
        s.cursor_agent_repo_url = "https://github.com/saorjuela14-lab/ACCIONESBUSQUEDA"
        s.cursor_agent_starting_ref = "main"
        s.cursor_agent_auto_create_pr = True
        s.cursor_agent_model = ""
        gs.return_value = s
        gsc.return_value = s

        response = httpx.Response(
            200,
            json={
                "agent": {"id": "bc-z", "url": "https://cursor.com/agents/bc-z", "status": "ACTIVE"},
                "run": {"id": "run-z", "status": "CREATING"},
            },
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=response)

        with patch("services.cursor_agent_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc.chat(
                "cambia el texto del depósito y quita lo de Alpaca",
                db,
                session_id="cursor-path",
            )

    assert result.mode == "cursor_agent"
    assert result.success is True
    assert "cursor.com/agents" in result.speech.lower() or "agente" in result.speech.lower()
