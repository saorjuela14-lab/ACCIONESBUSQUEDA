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
    "/logout",
)

# Clients may only monitor. All mutating API calls (except auth deposit request)
# require the mesa desk role.
CLIENT_MUTATION_ALLOW_PREFIXES = (
    "/api/v1/auth/deposit-request",
    "/api/v1/auth/capital/deposit",
    "/api/v1/auth/capital/withdraw",
    "/api/v1/auth/capital/mine",
    "/api/v1/auth/capital/funding",
    "/api/v1/auth/logout",
    "/api/v1/auth/client-error",
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

        # Login page: public. After explicit logout (?logged_out=1) never bounce back.
        if path == "/login" and method == "GET":
            forced_logout = request.query_params.get("logged_out") in ("1", "true", "yes")
            if not forced_logout:
                token = _extract_token(request)
                principal = await self._resolve(token) if token else None
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

        if path in ("/dashboard", "/dashboard/") and method == "GET":
            token = _extract_token(request)
            principal = await self._resolve(token) if token else None
            if not principal:
                return RedirectResponse(url="/login", status_code=302)
            if path == "/dashboard/":
                return RedirectResponse(url="/dashboard", status_code=302)
            request.state.principal = principal
            return await call_next(request)

        if path == "/logout" and method == "GET":
            return await call_next(request)

        token = _extract_token(request)
        principal = await self._resolve(token)

        if await self._auth_required():
            if not principal:
                metrics.inc("auth_failures")
                return JSONResponse(status_code=401, content={"detail": "Acceso no autorizado"})

        if principal:
            request.state.principal = principal
            # Approved clients are read-only monitors of the firm account.
            # Only the mesa invests / mutates capital, watchlist, broker, ops, etc.
            if (
                principal.get("role") != "desk"
                and method not in ("GET", "HEAD", "OPTIONS")
                and path.startswith("/api/")
                and not any(path.startswith(p) for p in CLIENT_MUTATION_ALLOW_PREFIXES)
            ):
                # Auth login/register stay public via PUBLIC_PREFIXES; other auth
                # mutations for clients are already limited inside the routes.
                if path.startswith("/api/v1/auth/"):
                    pass
                else:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": (
                                "Solo lectura: la mesa Monarch es quien invierte. "
                                "Tu acceso es para monitorear la cuenta."
                            )
                        },
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
