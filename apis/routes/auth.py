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


class CapitalAmountBody(BaseModel):
    amount_usd: float = Field(gt=0, le=1_000_000_000)
    note: str = Field(default="", max_length=280)


class CapitalDeskActionBody(BaseModel):
    desk_note: str = Field(default="", max_length=280)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=8, max_length=128)
    # Optional: ties reset to the same forgot request started in this browser
    request_id: str | None = Field(default=None, max_length=36)


class DeskSetPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    new_password: str = Field(min_length=8, max_length=128)


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


def _require_client(resolved: dict | None) -> dict:
    if not resolved or resolved.get("role") == "desk":
        raise HTTPException(status_code=403, detail="Solo un cliente autorizado puede hacer esto")
    if not resolved.get("org_id") or not resolved.get("user_id"):
        raise HTTPException(status_code=403, detail="Sesión sin empresa")
    return resolved


def _require_desk(resolved: dict | None) -> dict:
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa Monarch puede hacer esto")
    return resolved


async def _notify_mesa(title: str, body: str) -> None:
    try:
        from services.push_notification_service import PushNotificationService

        await PushNotificationService().notify_message(title, body)
    except Exception:
        pass


@router.get("/auth/capital/funding")
async def capital_funding_info(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Bank-transfer destination for the shared firm account (no Alpaca login)."""
    from services.capital_request_service import funding_package

    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    _require_client(resolved)
    return {"ok": True, "funding": funding_package(client_email=resolved.get("email") or "")}


@router.post("/auth/capital/deposit")
async def capital_deposit(
    body: CapitalAmountBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Client starts a deposit → gets bank transfer details for the firm account."""
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = _require_client(await auth.resolve_bearer(token) if token else None)
    try:
        result = await CapitalRequestService(session).request_deposit(
            org_id=resolved["org_id"],
            user_id=resolved["user_id"],
            amount_usd=body.amount_usd,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _notify_mesa(
        "Depósito solicitado (transferencia bancaria)",
        (
            f"Cliente: {resolved.get('email')}\n"
            f"Monto: ${body.amount_usd:,.2f}\n"
            f"Ref/memo: {resolved.get('email')}\n"
            f"{(body.note or '')[:200]}\n"
            f"El cliente recibió los datos bancarios de Monarch (sin login Alpaca)."
        ),
    )
    metrics.inc("capital_deposit_requested")
    return result


@router.post("/auth/capital/deposit/{request_id}/confirm")
async def capital_deposit_confirm(
    request_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Client says funds were sent — mesa gets confirmation to verify in Alpaca."""
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = _require_client(await auth.resolve_bearer(token) if token else None)
    try:
        result = await CapitalRequestService(session).client_confirm_deposit(
            org_id=resolved["org_id"],
            user_id=resolved["user_id"],
            request_id=request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    req = result.get("request") or {}
    await _notify_mesa(
        "Cliente confirma depósito enviado",
        (
            f"Cliente: {resolved.get('email')}\n"
            f"Monto: ${float(req.get('amount_usd') or 0):,.2f}\n"
            f"Revisa Alpaca y marca «Recibido» en Capital si ya entró el dinero."
        ),
    )
    metrics.inc("capital_deposit_client_confirmed")
    return result


@router.post("/auth/capital/withdraw")
async def capital_withdraw(
    body: CapitalAmountBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Client requests a withdrawal — mesa must approve before paying out."""
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = _require_client(await auth.resolve_bearer(token) if token else None)
    try:
        result = await CapitalRequestService(session).request_withdrawal(
            org_id=resolved["org_id"],
            user_id=resolved["user_id"],
            amount_usd=body.amount_usd,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _notify_mesa(
        "Retiro solicitado — requiere tu aprobación",
        (
            f"Cliente: {resolved.get('email')}\n"
            f"Monto: ${body.amount_usd:,.2f}\n"
            f"{(body.note or '')[:200]}\n"
            f"Revisa en Accesos → Capital y Aprueba o Rechaza."
        ),
    )
    metrics.inc("capital_withdrawal_requested")
    return result


@router.get("/auth/capital/mine")
async def capital_mine(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = _require_client(await auth.resolve_bearer(token) if token else None)
    svc = CapitalRequestService(session)
    items = await svc.list_mine(
        org_id=resolved["org_id"],
        user_id=resolved["user_id"],
    )
    summary = await svc.client_capital_summary(
        org_id=resolved["org_id"],
        user_id=resolved["user_id"],
    )
    return {"ok": True, "items": items, "summary": summary}


@router.get("/auth/capital/requests")
async def capital_requests_desk(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    _require_desk(await auth.resolve_bearer(token) if token else None)
    items = await CapitalRequestService(session).list_all(limit=80)
    return {"ok": True, "items": items}


@router.post("/auth/capital/{request_id}/approve")
async def capital_approve(
    request_id: str,
    body: CapitalDeskActionBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    _require_desk(await auth.resolve_bearer(token) if token else None)
    try:
        return await CapitalRequestService(session).desk_set_status(
            request_id=request_id, status="approved", desk_note=body.desk_note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auth/capital/{request_id}/reject")
async def capital_reject(
    request_id: str,
    body: CapitalDeskActionBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    _require_desk(await auth.resolve_bearer(token) if token else None)
    try:
        return await CapitalRequestService(session).desk_set_status(
            request_id=request_id, status="rejected", desk_note=body.desk_note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auth/capital/{request_id}/received")
async def capital_mark_received(
    request_id: str,
    body: CapitalDeskActionBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Desk confirms deposit landed in the firm Alpaca account."""
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    _require_desk(await auth.resolve_bearer(token) if token else None)
    try:
        return await CapitalRequestService(session).desk_set_status(
            request_id=request_id, status="received", desk_note=body.desk_note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auth/capital/{request_id}/paid")
async def capital_mark_paid(
    request_id: str,
    body: CapitalDeskActionBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Desk confirms withdrawal was paid out from Alpaca."""
    from services.capital_request_service import CapitalRequestService

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    _require_desk(await auth.resolve_bearer(token) if token else None)
    try:
        return await CapitalRequestService(session).desk_set_status(
            request_id=request_id, status="paid", desk_note=body.desk_note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auth/deposit-request")
async def deposit_request(
    body: DepositRequestBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Compat alias → /auth/capital/deposit."""
    return await capital_deposit(body=CapitalAmountBody(amount_usd=body.amount_usd, note=body.note), request=request, session=session)


@router.post("/auth/companies/{org_id}/deposit-received")
async def deposit_received(
    org_id: str,
    body: DepositReceivedBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Compat: mark latest requested/confirmed deposit for org as received."""
    from services.capital_request_service import CapitalRequestService
    from sqlalchemy import select
    from database.models import CapitalRequestORM

    auth = CompanyAuthService(session)
    token = _extract_bearer(request)
    _require_desk(await auth.resolve_bearer(token) if token else None)
    r = await session.execute(
        select(CapitalRequestORM)
        .where(
            CapitalRequestORM.org_id == org_id,
            CapitalRequestORM.kind == "deposit",
            CapitalRequestORM.status.in_(("requested", "client_confirmed")),
        )
        .order_by(CapitalRequestORM.created_at.desc())
        .limit(1)
    )
    row = r.scalar_one_or_none()
    if row:
        return await CapitalRequestService(session).desk_set_status(
            request_id=row.id, status="received", desk_note=""
        )
    # Fallback to legacy org flag
    try:
        return await auth.mark_deposit_received(org_id, body.amount_usd)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/auth/password/forgot")
async def forgot_password(
    body: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Request a recovery code. Mesa is notified (Telegram/WhatsApp) to relay it."""
    svc = CompanyAuthService(session)
    result = await svc.request_password_reset(body.email)
    if result.get("created") and result.get("code"):
        try:
            from services.push_notification_service import PushNotificationService

            await PushNotificationService().notify_message(
                "Recuperación de contraseña",
                (
                    f"Cliente: {result.get('full_name') or '—'}\n"
                    f"Email: {result.get('email')}\n"
                    f"Empresa: {result.get('org_name') or '—'}\n"
                    f"Código: {result['code']}\n"
                    f"Válido {result.get('expires_minutes', 30)} min.\n"
                    f"Entrégalelo al cliente o revísalo en Accesos → Recuperaciones."
                ),
            )
        except Exception:
            pass
        metrics.inc("auth_password_reset_requested")
    # Never expose the code publicly; request_id only binds this browser to the email
    return {
        "ok": True,
        "message": result.get("message"),
        "request_id": result.get("request_id"),
        "email": (body.email or "").strip().lower(),
    }


@router.post("/auth/password/reset")
async def reset_password(
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    svc = CompanyAuthService(session)
    try:
        out = await svc.reset_password_with_code(
            email=body.email,
            code=body.code,
            new_password=body.new_password,
            request_id=body.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metrics.inc("auth_password_reset_ok")
    return out


@router.get("/auth/password/resets")
async def list_password_resets(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mesa: pending recovery codes to relay to clients."""
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa puede ver recuperaciones")
    items = await svc.list_password_resets()
    return {"ok": True, "items": items}


@router.post("/auth/password/desk-set")
async def desk_set_password(
    body: DeskSetPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mesa sets a new password for an approved client directly."""
    svc = CompanyAuthService(session)
    token = _extract_bearer(request)
    resolved = await svc.resolve_bearer(token) if token else None
    if not resolved or resolved.get("role") != "desk":
        raise HTTPException(status_code=403, detail="Solo la mesa puede restablecer contraseñas")
    try:
        return await svc.desk_set_password(email=body.email, new_password=body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
