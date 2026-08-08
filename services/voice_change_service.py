"""Queue product/code change requests from the voice assistant (optional GitHub issues)."""

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

_STORE = Path("data/voice_change_requests.json")


class VoiceChangeService:
    def _load(self) -> list[dict[str, Any]]:
        if not _STORE.exists():
            return []
        try:
            return json.loads(_STORE.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        _STORE.write_text(json.dumps(items[-100:], ensure_ascii=False, indent=2), encoding="utf-8")

    def list_recent(self, limit: int = 8) -> list[dict[str, Any]]:
        items = self._load()
        return list(reversed(items[-limit:]))

    async def queue_change(
        self,
        title: str,
        description: str,
        area: str = "product",
        open_github_issue: bool = True,
    ) -> dict[str, Any]:
        settings = get_settings()
        item: dict[str, Any] = {
            "id": str(uuid.uuid4())[:8],
            "title": (title or "Cambio solicitado por voz").strip()[:160],
            "description": (description or "").strip()[:4000],
            "area": (area or "product").strip()[:40],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "github_issue_url": None,
            "status": "queued",
        }

        if open_github_issue and settings.github_token and settings.github_repo:
            try:
                url = await self._create_github_issue(item)
                item["github_issue_url"] = url
                item["status"] = "github_issue" if url else "queued"
            except Exception as exc:
                logger.warning("voice.change.github_failed", error=str(exc))
                item["github_error"] = str(exc)[:200]

        items = self._load()
        items.append(item)
        self._save(items)
        return item

    async def _create_github_issue(self, item: dict[str, Any]) -> str | None:
        settings = get_settings()
        repo = settings.github_repo
        body = (
            f"**Solicitado por voz (Viernes)**\n\n"
            f"**Área:** {item['area']}\n\n"
            f"{item['description']}\n\n"
            f"_id: `{item['id']}`_"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={
                    "Authorization": f"Bearer {settings.github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "title": f"[Voz/{item['area']}] {item['title']}",
                    "body": body,
                    "labels": ["voice-request"],
                },
            )
            if r.status_code >= 400:
                # labels may not exist — retry without labels
                r = await client.post(
                    f"https://api.github.com/repos/{repo}/issues",
                    headers={
                        "Authorization": f"Bearer {settings.github_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={
                        "title": f"[Voz/{item['area']}] {item['title']}",
                        "body": body,
                    },
                )
            if r.status_code >= 400:
                raise RuntimeError(f"GitHub HTTP {r.status_code}: {r.text[:200]}")
            return r.json().get("html_url")
