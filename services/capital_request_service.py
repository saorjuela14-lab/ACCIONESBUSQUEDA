"""Client capital movements: fund shared Alpaca book + withdrawal approvals."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import CapitalRequestORM, OrganizationORM, UserORM, utc_now
from utils.logging import get_logger

logger = get_logger(__name__)


def funding_package(*, client_email: str = "") -> dict[str, Any]:
    """Instructions / links so the client can fund the firm Alpaca account."""
    s = get_settings()
    memo = (client_email or "tu-email@cliente.com").strip().lower()
    instructions = (s.alpaca_funding_instructions or "").strip()
    wire = (s.alpaca_funding_wire_details or "").strip()
    return {
        "account_name": s.alpaca_funding_account_name or "Monarch Capital",
        "funding_url": s.alpaca_funding_url or "https://app.alpaca.markets/",
        "instructions": instructions,
        "wire_details": wire,
        "memo_reference": memo,
        "paper": bool(s.effective_alpaca_paper),
        "steps": [
            "Abre el enlace de fondeo de la cuenta Alpaca de Monarch.",
            "Transfiere el monto (ACH o wire) hacia esa cuenta.",
            f"En la referencia/memo escribe exactamente: {memo}",
            "Cuando el banco confirme el envío, pulsa «Ya deposité» para avisarnos.",
        ],
    }


class CapitalRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        funding = funding_package(client_email=user.email)
        logger.info("capital.deposit_requested", email=user.email, amount=amount_usd)
        return {
            "ok": True,
            "request": self._serialize(row),
            "funding": funding,
            "message": (
                "Solicitud registrada. Usa el enlace e instrucciones para depositar "
                "en la cuenta Alpaca de Monarch. Luego pulsa «Ya deposité»."
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
