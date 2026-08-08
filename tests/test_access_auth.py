"""Access auth middleware tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.responses import Response

from apis.middleware.access_auth import AccessTokenMiddleware, _extract_token
from config.settings import get_settings


def test_extract_bearer():
    req = MagicMock()
    req.headers = {"authorization": "Bearer secret123"}
    req.query_params = {}
    req.cookies = {}
    assert _extract_token(req) == "secret123"


@pytest.mark.asyncio
async def test_middleware_allows_when_no_token_configured(monkeypatch, tmp_path):
    db = tmp_path / "auth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "")
    monkeypatch.setenv("FORCE_AUTH", "false")
    get_settings.cache_clear()

    async def call_next(request):
        return Response("ok")

    mw = AccessTokenMiddleware(app=MagicMock())
    mw._auth_required = AsyncMock(return_value=False)
    mw._resolve = AsyncMock(return_value=None)

    req = MagicMock()
    req.url.path = "/api/v1/dashboard"
    req.method = "GET"
    req.headers = {}
    req.query_params = {}
    req.cookies = {}
    resp = await mw.dispatch(req, call_next)
    assert resp.status_code == 200

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_middleware_redirects_dashboard_when_auth_required():
    async def call_next(request):
        return Response("ok")

    mw = AccessTokenMiddleware(app=MagicMock())
    mw._auth_required = AsyncMock(return_value=True)
    mw._resolve = AsyncMock(return_value=None)

    req = MagicMock()
    req.url.path = "/dashboard"
    req.method = "GET"
    req.headers = {}
    req.query_params = {}
    req.cookies = {}
    resp = await mw.dispatch(req, call_next)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")
