"""Client capital movements: fund shared Alpaca book + withdrawal approvals."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import CapitalRequestORM, OrganizationORM, UserORM, utc_now
from utils.logging import get_logger

logger = get_logger(__name__)

# Never send clients to Alpaca auth — that forces signup/login per person.
_BLOCKED_FUNDING_HOSTS = {
    "app.alpaca.markets",
    "alpaca.markets",
    "broker-app.alpaca.markets",
}


def _safe_funding_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    if not host or host in _BLOCKED_FUNDING_HOSTS or host.endswith(".alpaca.markets"):
        return ""
    return url


def funding_package(*, client_email: str = "", amount_usd: float | None = None) -> dict[str, Any]:
    """Bank-transfer destination for the ONE shared firm account (no Alpaca login)."""
    s = get_settings()
    memo = (client_email or "tu-email@cliente.com").strip().lower()
    bank_name = (s.alpaca_funding_bank_name or "").strip()
    routing = (s.alpaca_funding_routing_number or "").strip()
    account_no = (s.alpaca_funding_account_number or "").strip()
    beneficiary = (s.alpaca_funding_beneficiary or s.alpaca_funding_account_name or "Monarch Capital").strip()
    account_type = (s.alpaca_funding_account_type or "Checking").strip()
    swift = (s.alpaca_funding_swift or "").strip()
    instructions = (s.alpaca_funding_instructions or "").strip()
    wire = (s.alpaca_funding_wire_details or "").strip()
    funding_url = _safe_funding_url(s.alpaca_funding_url)
    configured = bool(bank_name and routing and account_no) or bool(wire)

    bank = {
        "beneficiary": beneficiary,
        "bank_name": bank_name,
        "routing_number": routing,
        "account_number": account_no,
        "account_type": account_type,
        "swift": swift,
    }
    return {
        "account_name": s.alpaca_funding_account_name or "Monarch Capital",
        "funding_url": funding_url,  # empty unless a non-Alpaca custom page is configured
        "bank": bank,
        "instructions": instructions,
        "wire_details": wire,
        "memo_reference": memo,
        "amount_usd": amount_usd,
        "configured": configured,
        "shared_account": True,
        "no_alpaca_login": True,
        "paper": bool(s.effective_alpaca_paper),
        "headline": (
            "Transfiere desde TU banco hacia los datos de Monarch. "
            "No abras Alpaca, no pulses «Select» ni conectes tu cuenta bancaria allí — eso solo lo hace la mesa."
        ),
        "steps": [
            "Abre la app o web de TU banco (no Alpaca).",
            "Crea una transferencia ACH o wire HACIA los datos de abajo (copiar/pegar).",
            f"En referencia/memo escribe exactamente: {memo}",
            "Cuando tu banco confirme el envío, pulsa «Ya deposité». La mesa verifica y marca recibido.",
        ],
        "desk_only_note": (
            "La mesa obtiene estos datos en Alpaca → Funds → Incoming wire / ACH details. "
            "El flujo «Deposit Funds → Select → login al banco» es solo para el dueño de la cuenta Alpaca."
        ),
    }


class CapitalRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def client_capital_summary(
        self,
        *,
        org_id: str,
        user_id: str,
        firm_return_pct: float | None = None,
    ) -> dict[str, Any]:
        """Per-client capital (received deposits − paid withdrawals) + account return %.

        Clients never see the firm book total — only their net contribution and
        the desk account's performance percentage.
        """
        await self._require_active_client(org_id=org_id, user_id=user_id)
        r = await self._session.execute(
            select(CapitalRequestORM).where(
                CapitalRequestORM.org_id == org_id,
                CapitalRequestORM.user_id == user_id,
            )
        )
        rows = list(r.scalars().all())
        deposited = sum(x.amount_usd for x in rows if x.kind == "deposit" and x.status == "received")
        withdrawn = sum(x.amount_usd for x in rows if x.kind == "withdrawal" and x.status == "paid")
        pending_deposit = sum(
            x.amount_usd
            for x in rows
            if x.kind == "deposit" and x.status in ("requested", "client_confirmed")
        )
        pending_withdrawal = sum(
            x.amount_usd
            for x in rows
            if x.kind == "withdrawal" and x.status in ("requested", "approved")
        )
        net = max(0.0, float(deposited) - float(withdrawn))
        has_invested = net > 0
        ret = float(firm_return_pct) if firm_return_pct is not None else None
        estimated_equity = round(net * (1.0 + (ret or 0.0) / 100.0), 2) if has_invested else None
        estimated_pnl = round(estimated_equity - net, 2) if estimated_equity is not None else None
        return {
            "has_invested": has_invested,
            "mode": "investor" if has_invested else "prospect",
            "deposited_usd": round(float(deposited), 2),
            "withdrawn_usd": round(float(withdrawn), 2),
            "net_capital_usd": round(net, 2),
            "pending_deposit_usd": round(float(pending_deposit), 2),
            "pending_withdrawal_usd": round(float(pending_withdrawal), 2),
            "firm_return_pct": ret,
            "estimated_equity_usd": estimated_equity,
            "estimated_pnl_usd": estimated_pnl,
            "note": (
                "Tu capital es lo que la mesa ya confirmó recibido. "
                "El rendimiento % es el de la cuenta Monarch (compartida); "
                "no ves el total del portafolio de la mesa."
            ),
        }

    async def _require_active_client(self, *, org_id: str, user_id: str) -> tuple[OrganizationORM, UserORM]:
        org_r = await self._session.execute(select(OrganizationORM).where(OrganizationORM.id == org_id))
        org = org_r.scalar_one_or_none()
        if not org or not org.active or org.id == "monarch":
            raise ValueError("Tu acceso aún no está autorizado")
        user_r = await self._session.execute(select(UserORM).where(UserORM.id == user_id))
        user = user_r.scalar_one_or_none()
        if not user or not user.active or user.org_id != org.id:
            raise ValueError("Sesión inválida")
        return org, user

    def _serialize(self, row: CapitalRequestORM) -> dict[str, Any]:
        return {
            "id": row.id,
            "org_id": row.org_id,
            "user_id": row.user_id,
            "email": row.email,
            "kind": row.kind,
            "amount_usd": row.amount_usd,
            "note": row.note,
            "status": row.status,
            "desk_note": row.desk_note,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def request_deposit(
        self,
        *,
        org_id: str,
        user_id: str,
        amount_usd: float,
        note: str = "",
    ) -> dict[str, Any]:
        if amount_usd <= 0:
            raise ValueError("Indica un monto de depósito mayor a 0")
        org, user = await self._require_active_client(org_id=org_id, user_id=user_id)
        row = CapitalRequestORM(
            id=str(uuid.uuid4()),
            org_id=org.id,
            user_id=user.id,
            email=user.email,
            kind="deposit",
            amount_usd=float(amount_usd),
            note=(note or "")[:280] or None,
            status="requested",
        )
        self._session.add(row)
        org.deposit_status = "requested"
        org.deposit_requested_usd = float(amount_usd)
        org.deposit_note = row.note
        await self._session.commit()
        funding = funding_package(client_email=user.email, amount_usd=float(amount_usd))
        logger.info("capital.deposit_requested", email=user.email, amount=amount_usd)
        return {
            "ok": True,
            "request": self._serialize(row),
            "funding": funding,
            "message": (
                "Listo. Transfiere a los datos bancarios de Monarch (sin entrar a Alpaca). "
                "Luego pulsa «Ya deposité»."
            ),
        }

    async def client_confirm_deposit(self, *, org_id: str, user_id: str, request_id: str) -> dict[str, Any]:
        org, user = await self._require_active_client(org_id=org_id, user_id=user_id)
        row = await self._get_owned(request_id, org_id=org.id, user_id=user.id, kind="deposit")
        if row.status not in ("requested", "client_confirmed"):
            raise ValueError("Esta solicitud ya fue cerrada")
        row.status = "client_confirmed"
        row.updated_at = utc_now()
        org.deposit_status = "client_confirmed"
        await self._session.commit()
        return {
            "ok": True,
            "request": self._serialize(row),
            "message": "Aviso enviado a la mesa. Confirmarán cuando el dinero aparezca en Alpaca.",
        }

    async def request_withdrawal(
        self,
        *,
        org_id: str,
        user_id: str,
        amount_usd: float,
        note: str = "",
    ) -> dict[str, Any]:
        if amount_usd <= 0:
            raise ValueError("Indica un monto de retiro mayor a 0")
        org, user = await self._require_active_client(org_id=org_id, user_id=user_id)
        row = CapitalRequestORM(
            id=str(uuid.uuid4()),
            org_id=org.id,
            user_id=user.id,
            email=user.email,
            kind="withdrawal",
            amount_usd=float(amount_usd),
            note=(note or "")[:280] or None,
            status="requested",
        )
        self._session.add(row)
        org.withdrawal_status = "requested"
        org.withdrawal_requested_usd = float(amount_usd)
        org.withdrawal_note = row.note
        await self._session.commit()
        logger.info("capital.withdrawal_requested", email=user.email, amount=amount_usd)
        return {
            "ok": True,
            "request": self._serialize(row),
            "message": "Solicitud de retiro enviada. La mesa Monarch la revisará y aprobará si corresponde.",
        }

    async def list_mine(self, *, org_id: str, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        await self._require_active_client(org_id=org_id, user_id=user_id)
        r = await self._session.execute(
            select(CapitalRequestORM)
            .where(CapitalRequestORM.org_id == org_id, CapitalRequestORM.user_id == user_id)
            .order_by(CapitalRequestORM.created_at.desc())
            .limit(limit)
        )
        return [self._serialize(x) for x in r.scalars().all()]

    async def list_all(self, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        q = select(CapitalRequestORM).order_by(CapitalRequestORM.created_at.desc()).limit(limit)
        if status:
            q = (
                select(CapitalRequestORM)
                .where(CapitalRequestORM.status == status)
                .order_by(CapitalRequestORM.created_at.desc())
                .limit(limit)
            )
        r = await self._session.execute(q)
        return [self._serialize(x) for x in r.scalars().all()]

    async def desk_set_status(
        self,
        *,
        request_id: str,
        status: str,
        desk_note: str = "",
    ) -> dict[str, Any]:
        allowed = {
            "deposit": {"received", "rejected", "client_confirmed"},
            "withdrawal": {"approved", "rejected", "paid"},
        }
        r = await self._session.execute(select(CapitalRequestORM).where(CapitalRequestORM.id == request_id))
        row = r.scalar_one_or_none()
        if not row:
            raise ValueError("Solicitud no encontrada")
        if status not in allowed.get(row.kind, set()):
            raise ValueError(f"Estado inválido para {row.kind}: {status}")
        row.status = status
        row.desk_note = (desk_note or "")[:280] or row.desk_note
        row.updated_at = utc_now()

        org_r = await self._session.execute(select(OrganizationORM).where(OrganizationORM.id == row.org_id))
        org = org_r.scalar_one_or_none()
        if org:
            if row.kind == "deposit":
                org.deposit_status = status if status != "rejected" else "none"
                if status == "received":
                    org.deposit_requested_usd = row.amount_usd
            else:
                org.withdrawal_status = status if status != "rejected" else "none"
                if status in ("approved", "paid"):
                    org.withdrawal_requested_usd = row.amount_usd
        await self._session.commit()
        return {"ok": True, "request": self._serialize(row)}

    async def _get_owned(
        self,
        request_id: str,
        *,
        org_id: str,
        user_id: str,
        kind: str,
    ) -> CapitalRequestORM:
        r = await self._session.execute(select(CapitalRequestORM).where(CapitalRequestORM.id == request_id))
        row = r.scalar_one_or_none()
        if not row or row.org_id != org_id or row.user_id != user_id or row.kind != kind:
            raise ValueError("Solicitud no encontrada")
        return row
