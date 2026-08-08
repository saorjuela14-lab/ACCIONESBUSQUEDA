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


class DepositRequestBody(BaseModel):
    amount_usd: float = Field(gt=0, le=1_000_000_000)
    note: str = Field(default="", max_length=280)


class DepositReceivedBody(BaseModel):
    amount_usd: float | None = Field(default=None, gt=0, le=1_000_000_000)


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


def _clear_session_cookies(response: Response) -> None:
    """Expire auth cookies with the same flags used when setting them."""
    settings = get_settings()
    secure = settings.app_env == "production"
    for key in ("nexbuy_token", "monarch_token"):
        response.delete_cookie(key=key, path="/", secure=secure, samesite="lax")
        # Fallback without secure (covers mismatched env / proxy quirks)
        response.delete_cookie(key=key, path="/")


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
    """Solicitud de acceso (público) o alta inmediata si la crea la mesa.

    Público → org pendiente (active=False), sin sesión, notifica a la mesa.
    Mesa → org aprobada de una vez (solo lectura para el cliente).
    No se crea portafolio ni capital inventado ($1000).
    """
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    is_desk = False
    if token:
        resolved = await svc.resolve_bearer(token)
        is_desk = bool(resolved and resolved.get("role") == "desk")

    pending = not is_desk
    try:
        org, user, raw = await svc.create_company(
            org_name=body.org_name,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            role="viewer",
            pending=pending,
            issue_session=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if pending:
        # Notify mesa (Telegram/WhatsApp env) — never block registration on push failure
        try:
            from services.push_notification_service import PushNotificationService

            await PushNotificationService().notify_message(
                "Solicitud de acceso Monarch",
                (
                    f"Empresa: {org.name}\n"
                    f"Contacto: {user.full_name or '—'}\n"
                    f"Email: {user.email}\n"
                    f"Estado: pendiente de autorización.\n"
                    f"Entra a la mesa → panel Accesos para aprobar."
                ),
            )
        except Exception:
            pass
        metrics.inc("auth_access_requests")
        return {
            "ok": True,
            "pending": True,
            "status": "pending",
            "message": (
                "Solicitud enviada. La mesa Monarch debe autorizar tu acceso. "
                "Cuando te aprueben podrás entrar a monitorear la cuenta y solicitar depósito. "
                "Solo la mesa invierte."
            ),
            "organization": {"id": org.id, "name": org.name, "slug": org.slug, "active": False},
            "user": {"id": user.id, "email": user.email, "role": user.role},
        }

    metrics.inc("auth_companies_created")
    return {
        "ok": True,
        "pending": False,
        "status": "approved",
        "message": "Cliente creado y autorizado (solo lectura).",
        "organization": {"id": org.id, "name": org.name, "slug": org.slug, "active": True},
        "user": {"id": user.id, "email": user.email, "role": user.role},
    }


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


@router.post("/auth/companies/{org_id}/approve")
async def approve_company(
    org_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa puede autorizar accesos")
    try:
        return await svc.approve_company(org_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/auth/companies/{org_id}/reject")
async def reject_company(
    org_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa puede rechazar accesos")
    try:
        return await svc.reject_company(org_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/auth/deposit-request")
async def deposit_request(
    body: DepositRequestBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cliente aprobado solicita depositar; la mesa invierte el capital."""
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") == "desk":
        raise HTTPException(status_code=403, detail="Solo un cliente autorizado puede solicitar depósito")
    org_id = resolved.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="Sesión sin empresa")
    try:
        result = await svc.request_deposit(
            org_id=org_id,
            amount_usd=body.amount_usd,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        from services.push_notification_service import PushNotificationService

        await PushNotificationService().notify_message(
            "Depósito solicitado",
            (
                f"Cliente: {resolved.get('email')}\n"
                f"Monto: ${body.amount_usd:,.2f}\n"
                f"{(body.note or '')[:200]}"
            ),
        )
    except Exception:
        pass
    return result


@router.post("/auth/companies/{org_id}/deposit-received")
async def deposit_received(
    org_id: str,
    body: DepositReceivedBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa puede confirmar depósitos")
    try:
        return await svc.mark_deposit_received(org_id, body.amount_usd)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
        try:
            await CompanyAuthService(session).revoke_token(token)
        except Exception:
            pass
    _clear_session_cookies(response)
    return {"ok": True, "logged_out": True}


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
