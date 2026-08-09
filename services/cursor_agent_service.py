"""Launch and track Cursor Cloud Agents from Viernes (desk voice).

Uses Cloud Agents API v1: https://api.cursor.com/v1/agents
Auth: Basic (API key as username, empty password) or Bearer.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from config.settings import get_settings
from utils.logging import get_logger

logger = get_logger(__name__)

_API = "https://api.cursor.com/v1"
_STORE = Path("data/cursor_agent_launches.json")


class CursorAgentError(Exception):
    """Raised when the Cursor Cloud Agents API rejects a call."""


class CursorAgentService:
    def configured(self) -> bool:
        s = get_settings()
        return bool((s.cursor_api_key or "").strip()) and bool(s.cursor_agent_enabled)

    def status(self) -> dict[str, Any]:
        s = get_settings()
        key = (s.cursor_api_key or "").strip()
        return {
            "configured": self.configured(),
            "enabled": bool(s.cursor_agent_enabled),
            "api_key_present": bool(key),
            "repo_url": s.cursor_agent_repo_url,
            "starting_ref": s.cursor_agent_starting_ref,
            "auto_create_pr": bool(s.cursor_agent_auto_create_pr),
            "model": s.cursor_agent_model or None,
            "hint": (
                None
                if self.configured()
                else "Define CURSOR_API_KEY en FastAPI Cloud (Dashboard → API Keys)."
            ),
        }

    def _auth_headers(self) -> tuple[tuple[str, str] | None, dict[str, str]]:
        key = (get_settings().cursor_api_key or "").strip()
        # Cloud Agents API accepts Basic (key as username) or Bearer
        return (key, ""), {"Accept": "application/json"}

    def _load(self) -> list[dict[str, Any]]:
        if not _STORE.exists():
            return []
        try:
            return json.loads(_STORE.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        _STORE.write_text(json.dumps(items[-80:], ensure_ascii=False, indent=2), encoding="utf-8")

    def list_launches(self, limit: int = 8) -> list[dict[str, Any]]:
        items = self._load()
        return list(reversed(items[-limit:]))

    def _record(self, item: dict[str, Any]) -> dict[str, Any]:
        items = self._load()
        items.append(item)
        self._save(items)
        return item

    async def launch(
        self,
        *,
        prompt: str,
        title: str = "",
        area: str = "product",
        auto_create_pr: bool | None = None,
    ) -> dict[str, Any]:
        if not self.configured():
            raise CursorAgentError("CURSOR_API_KEY no configurada o CURSOR_AGENT_ENABLED=false")

        text = (prompt or "").strip()
        if len(text) < 8:
            raise CursorAgentError("El prompt para el agente es demasiado corto")

        s = get_settings()
        name = (title or text[:80]).strip()[:100]
        body: dict[str, Any] = {
            "prompt": {"text": self._wrap_prompt(text, area=area)},
            "name": name or "Viernes → Cursor",
            "repos": [
                {
                    "url": s.cursor_agent_repo_url,
                    "startingRef": s.cursor_agent_starting_ref or "main",
                }
            ],
            "autoCreatePR": (
                bool(s.cursor_agent_auto_create_pr) if auto_create_pr is None else bool(auto_create_pr)
            ),
            "mode": "agent",
        }
        model_id = (s.cursor_agent_model or "").strip()
        if model_id:
            body["model"] = {"id": model_id}

        auth, headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_API}/agents",
                auth=auth,
                headers=headers,
                json=body,
            )
        if r.status_code >= 400:
            logger.warning("cursor.agent.launch_failed", status=r.status_code, body=r.text[:400])
            raise CursorAgentError(self._err_message(r))

        data = r.json()
        agent = data.get("agent") or {}
        run = data.get("run") or {}
        item = {
            "id": str(uuid.uuid4())[:8],
            "local_id": str(uuid.uuid4())[:8],
            "title": name,
            "area": (area or "product")[:40],
            "prompt_preview": text[:280],
            "agent_id": agent.get("id"),
            "run_id": run.get("id") or agent.get("latestRunId"),
            "agent_url": agent.get("url"),
            "agent_status": agent.get("status"),
            "run_status": run.get("status"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "viernes",
        }
        self._record(item)
        logger.info("cursor.agent.launched", agent_id=item["agent_id"], run_id=item["run_id"])
        return {
            "ok": True,
            "agent_id": item["agent_id"],
            "run_id": item["run_id"],
            "url": item["agent_url"],
            "status": item["run_status"] or item["agent_status"],
            "title": item["title"],
            "speech_hint": (
                f"Ya lancé un agente de Cursor en el repo. "
                f"Puedes seguirlo en {item['agent_url'] or 'cursor.com/agents'}. "
                f"Cuando termine abre PR si está activado."
            ),
            "launch": item,
        }

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        aid = (agent_id or "").strip()
        if not aid:
            raise CursorAgentError("Falta agent_id")
        if not self.configured():
            raise CursorAgentError("CURSOR_API_KEY no configurada")
        auth, headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{_API}/agents/{aid}", auth=auth, headers=headers)
        if r.status_code >= 400:
            raise CursorAgentError(self._err_message(r))
        agent = r.json()
        run_info = None
        latest = agent.get("latestRunId")
        if latest:
            try:
                run_info = await self.get_run(aid, latest)
            except Exception as exc:
                run_info = {"error": str(exc)[:200]}
        return {"ok": True, "agent": agent, "latest_run": run_info}

    async def get_run(self, agent_id: str, run_id: str) -> dict[str, Any]:
        auth, headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{_API}/agents/{agent_id}/runs/{run_id}",
                auth=auth,
                headers=headers,
            )
        if r.status_code >= 400:
            raise CursorAgentError(self._err_message(r))
        return r.json()

    async def follow_up(self, agent_id: str, prompt: str) -> dict[str, Any]:
        aid = (agent_id or "").strip()
        text = (prompt or "").strip()
        if not aid or len(text) < 4:
            raise CursorAgentError("Necesito agent_id y un follow-up claro")
        if not self.configured():
            raise CursorAgentError("CURSOR_API_KEY no configurada")
        auth, headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_API}/agents/{aid}/runs",
                auth=auth,
                headers=headers,
                json={"prompt": {"text": text}, "mode": "agent"},
            )
        if r.status_code >= 400:
            raise CursorAgentError(self._err_message(r))
        data = r.json()
        run = data.get("run") or data
        return {
            "ok": True,
            "agent_id": aid,
            "run_id": run.get("id"),
            "status": run.get("status"),
            "url": f"https://cursor.com/agents/{aid}",
        }

    async def list_remote(self, limit: int = 10) -> dict[str, Any]:
        if not self.configured():
            raise CursorAgentError("CURSOR_API_KEY no configurada")
        auth, headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{_API}/agents",
                auth=auth,
                headers=headers,
                params={"limit": max(1, min(limit, 50)), "includeArchived": "false"},
            )
        if r.status_code >= 400:
            raise CursorAgentError(self._err_message(r))
        data = r.json()
        return {"ok": True, "items": data.get("items") or [], "local_launches": self.list_launches(limit)}

    def _wrap_prompt(self, text: str, *, area: str) -> str:
        return (
            "Eres el agente de implementación de Monarch Capital, disparado por Viernes "
            "(asistente de voz de la mesa / CEO Sergio Orjuela).\n"
            f"Área: {area or 'product'}\n"
            "Repo: ACCIONESBUSQUEDA — terminal de inversión Monarch Capital.\n"
            "Reglas: cambios mínimos y enfocados; no rompas auth/capital client view; "
            "branch cursor/*-1bd5; commit + push + PR a main cuando corresponda; "
            "responde en español al final con resumen breve.\n\n"
            f"Pedido del jefe:\n{text.strip()}"
        )

    def _err_message(self, r: httpx.Response) -> str:
        try:
            detail = r.json()
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("error") or detail)[:300]
        except Exception:
            pass
        return f"Cursor API HTTP {r.status_code}: {r.text[:240]}"
