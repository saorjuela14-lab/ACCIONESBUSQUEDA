"""Access control: desk token and/or company session bearers."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from config.settings import get_settings
from utils.metrics import metrics

# Entire static tree is public (JS/CSS must load without Bearer).
PUBLIC_PREFIXES = (
    "/health",
    "/metrics",
    "/api/v1/auth/",
    "/dashboard/static/",
)

DESK_WRITE_PREFIXES = (
    "/api/v1/ops/kill-switch",
    "/api/v1/ops/autopilot",
    "/api/v1/ops/intraday/flat",
    "/api/v1/broker/execute",
    "/api/v1/auth/companies",
)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request.headers.get("x-access-token"):
        return request.headers.get("x-access-token")
    cookie = request.cookies.get("nexbuy_token") or request.cookies.get("monarch_token")
    if cookie:
        return cookie
    return request.query_params.get("token")


class AccessTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path
        method = request.method.upper()

        if settings.app_env == "production" and not settings.expose_api_docs:
            if path in ("/docs", "/openapi.json", "/redoc"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})

        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # Login page: public, but bounce already-authenticated users to the terminal
        if path == "/login" and method == "GET":
            if await self._auth_required():
                token = _extract_token(request)
                principal = await self._resolve(token)
                if principal:
                    return RedirectResponse(url="/dashboard", status_code=302)
            return await call_next(request)

        # Entry + terminal HTML: login first — never paint the desk without a session
        if path == "/" and method == "GET":
            token = _extract_token(request)
            principal = await self._resolve(token) if token else None
            if principal:
                return RedirectResponse(url="/dashboard", status_code=302)
            return RedirectResponse(url="/login", status_code=302)

        if path == "/dashboard" and method == "GET":
            token = _extract_token(request)
            principal = await self._resolve(token) if token else None
            if not principal:
                return RedirectResponse(url="/login", status_code=302)
            request.state.principal = principal
            return await call_next(request)

        token = _extract_token(request)
        principal = await self._resolve(token)

        if await self._auth_required():
            if not principal:
                metrics.inc("auth_failures")
                return JSONResponse(status_code=401, content={"detail": "Acceso no autorizado"})

        if principal:
            request.state.principal = principal
            if principal.get("role") != "desk" and method != "GET":
                if any(path.startswith(p) for p in DESK_WRITE_PREFIXES):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Solo la mesa Monarch puede ejecutar esta acción"},
                    )

        metrics.inc("http_requests")
        response = await call_next(request)
        if response.status_code >= 500:
            metrics.inc("http_5xx")
        elif response.status_code >= 400:
            metrics.inc("http_4xx")
        return response

    async def _auth_required(self) -> bool:
        settings = get_settings()
        if settings.force_auth or settings.dashboard_access_token:
            return True
        from database.engine import get_session
        from services.company_auth_service import CompanyAuthService

        try:
            async for session in get_session():
                return await CompanyAuthService(session).auth_required()
        except Exception:
            return bool(settings.force_auth)
        return False

    async def _resolve(self, token: str | None) -> dict | None:
        if not token:
            return None
        settings = get_settings()
        desk = settings.dashboard_access_token or ""
        if desk and hmac.compare_digest(token, desk):
            return {
                "auth_type": "desk",
                "role": "desk",
                "user_id": "desk",
                "org_id": "monarch",
                "email": "desk@monarch",
            }
        from database.engine import get_session
        from services.company_auth_service import CompanyAuthService

        try:
            async for session in get_session():
                return await CompanyAuthService(session).resolve_bearer(token)
        except Exception:
            return None
        return None
