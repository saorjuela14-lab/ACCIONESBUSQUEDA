"""Auth: desk token + B2B company email/password sessions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.engine import get_session
from domain.pagination import Page
from services.company_auth_service import CompanyAuthService
from utils.metrics import metrics

router = APIRouter()


class DeskLoginRequest(BaseModel):
    token: str = Field(min_length=1)


class CompanyLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class CompanyCreateRequest(BaseModel):
    org_name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=160)


class ClientErrorReport(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="window", max_length=64)
    url: str | None = Field(default=None, max_length=500)
    stack: str | None = Field(default=None, max_length=4000)


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("nexbuy_token") or request.cookies.get("monarch_token")


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key="nexbuy_token",
        value=token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=14 * 24 * 3600,
        path="/",
    )


@router.get("/auth/status")
async def auth_status(session: AsyncSession = Depends(get_session)) -> dict:
    settings = get_settings()
    svc = CompanyAuthService(session)
    required = await svc.auth_required()
    return {
        "auth_required": required,
        "app_name": settings.app_name,
        "modes": {
            "desk_token": bool(settings.dashboard_access_token),
            "company_login": True,
            "company_users": await svc.user_count(),
        },
    }


@router.post("/auth/login")
async def login_desk(
    request: DeskLoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Compat: login con token de mesa (DASHBOARD_ACCESS_TOKEN)."""
    try:
        result = await CompanyAuthService(session).login_desk_token(request.token)
    except ValueError as exc:
        metrics.inc("auth_failures")
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, result["token"])
    metrics.inc("auth_logins_desk")
    return result


@router.post("/auth/company/login")
async def login_company(
    body: CompanyLoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await CompanyAuthService(session).login_email(body.email, body.password)
    except ValueError as exc:
        metrics.inc("auth_failures")
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, result["token"])
    metrics.inc("auth_logins_company")
    return result


@router.post("/auth/companies")
async def create_company(
    body: CompanyCreateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Crear empresa. Permitido: mesa (desk) o bootstrap cuando no hay usuarios."""
    svc = CompanyAuthService(session)
    users = await svc.user_count()
    principal = getattr(request.state, "principal", None)
    token = _extract_bearer(request)
    is_desk = False
    if token:
        resolved = await svc.resolve_bearer(token)
        is_desk = bool(resolved and resolved.get("role") == "desk")
        principal = resolved or principal

    if users > 0 and not is_desk:
        raise HTTPException(status_code=403, detail="Solo la mesa puede crear empresas")

    try:
        org, user, raw = await svc.create_company(
            org_name=body.org_name,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Seed an empty company book so the terminal is never "Sin portafolio"
    try:
        from providers.market.factory import get_market_provider
        from services.portfolio_bootstrap_service import PortfolioBootstrapService
        from services.portfolio_service import PortfolioService
        from database.repositories.portfolio_repository import PortfolioRepository

        boot = PortfolioBootstrapService(
            PortfolioService(PortfolioRepository(session), get_market_provider())
        )
        await boot.ensure_portfolio(
            org_id=org.id,
            allow_alpaca=False,
            default_name=f"Portafolio {org.name}"[:120],
            default_cash=1000.0,
        )
    except Exception:
        pass

    # If created by desk, don't replace desk cookie with company session
    out = {
        "ok": True,
        "organization": {"id": org.id, "name": org.name, "slug": org.slug},
        "user": {"id": user.id, "email": user.email, "role": user.role},
    }
    if not is_desk:
        out["token"] = raw
        out["auth_type"] = "session"
        _set_session_cookie(response, raw)
    metrics.inc("auth_companies_created")
    return out


@router.get("/auth/companies")
async def list_companies(
    request: Request,
    limit: int = 25,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> Page[dict]:
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa puede listar empresas")
    items, total = await svc.list_companies(limit=min(limit, 100), offset=max(offset, 0))
    return Page.of(items, total=total, limit=min(limit, 100), offset=max(offset, 0))


@router.get("/auth/me")
async def auth_me(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    token = _extract_bearer(request)
    resolved = await CompanyAuthService(session).resolve_bearer(token) if token else None
    if not resolved:
        raise HTTPException(status_code=401, detail="No autenticado")
    return {"ok": True, **resolved}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)) -> dict:
    token = _extract_bearer(request)
    if token:
        await CompanyAuthService(session).revoke_token(token)
    response.delete_cookie("nexbuy_token", path="/")
    response.delete_cookie("monarch_token", path="/")
    return {"ok": True}


@router.post("/auth/client-error")
async def client_error(body: ClientErrorReport, request: Request) -> dict:
    """Frontend error boundary → logs + metrics (no PII beyond message/url)."""
    from utils.logging import get_logger

    metrics.inc("client_errors")
    get_logger(__name__).warning(
        "client.error",
        message=body.message[:500],
        source=body.source,
        url=(body.url or "")[:200],
        stack=(body.stack or "")[:500],
        path=str(request.url.path),
    )
    return {"ok": True}
