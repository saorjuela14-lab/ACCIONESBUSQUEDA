"""Optional access token protection for public deployment."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.settings import get_settings

PUBLIC_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/v1/auth/",
    "/dashboard/static/login.html",
    "/dashboard/static/manifest.json",
    "/dashboard/static/sw.js",
    "/dashboard/static/icon.svg",
)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request.headers.get("x-access-token"):
        return request.headers.get("x-access-token")
    return request.query_params.get("token") or request.cookies.get("nexbuy_token")


class AccessTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.dashboard_access_token:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        if path in ("/dashboard", "/") and request.method == "GET":
            return await call_next(request)

        token = _extract_token(request)
        if token != settings.dashboard_access_token:
            return JSONResponse(status_code=401, content={"detail": "Acceso no autorizado"})

        return await call_next(request)
