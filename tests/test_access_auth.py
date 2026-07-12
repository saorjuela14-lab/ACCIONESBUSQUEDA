"""Access auth middleware tests."""

from unittest.mock import MagicMock

import pytest
from starlette.responses import Response

from apis.middleware.access_auth import AccessTokenMiddleware, _extract_token


def test_extract_bearer():
    req = MagicMock()
    req.headers = {"authorization": "Bearer secret123"}
    req.query_params = {}
    req.cookies = {}
    assert _extract_token(req) == "secret123"


@pytest.mark.asyncio
async def test_middleware_allows_when_no_token_configured(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "")
    get_settings.cache_clear()

    async def call_next(request):
        return Response("ok")

    mw = AccessTokenMiddleware(app=MagicMock())
    req = MagicMock()
    req.url.path = "/api/v1/dashboard"
    req.headers = {}
    req.query_params = {}
    req.cookies = {}
    resp = await mw.dispatch(req, call_next)
    assert resp.status_code == 200

    get_settings.cache_clear()
