"""B2B company auth — organizations, users, opaque sessions."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import AuthSessionORM, OrganizationORM, UserORM, utc_now
from utils.logging import get_logger

logger = get_logger(__name__)

_PBKDF2_ROUNDS = 120_000
_SESSION_DAYS = 14


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt, hexdigest = stored.split("$", 3)
        if algo != "pbkdf2":
            return False
        rounds = int(rounds_s)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
        return hmac.compare_digest(digest.hex(), hexdigest)
    except Exception:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "empresa")[:60]


class CompanyAuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_count(self) -> int:
        r = await self._session.execute(select(func.count()).select_from(UserORM))
        return int(r.scalar() or 0)

    async def auth_required(self) -> bool:
        settings = get_settings()
        if settings.force_auth:
            return True
        if settings.dashboard_access_token:
            return True
        return (await self.user_count()) > 0

    async def bootstrap_if_needed(self) -> dict[str, Any] | None:
        """Create first company from env if DB empty and bootstrap vars set."""
        settings = get_settings()
        if await self.user_count() > 0:
            return None
        email = (settings.company_bootstrap_email or "").strip().lower()
        password = settings.company_bootstrap_password or ""
        org_name = (settings.company_bootstrap_name or "Empresa demo").strip()
        if not email or not password:
            return None
        org, user, raw = await self.create_company(
            org_name=org_name,
            email=email,
            password=password,
            full_name="Admin",
            role="company_admin",
        )
        logger.info("auth.bootstrap_company", org=org.slug, email=user.email)
        return {"org_id": org.id, "email": user.email, "bootstrapped": True}

    async def create_company(
        self,
        *,
        org_name: str,
        email: str,
        password: str,
        full_name: str = "",
        role: str = "company_admin",
    ) -> tuple[OrganizationORM, UserORM, str]:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Email inválido")
        if len(password) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres")

        existing = await self._session.execute(select(UserORM).where(UserORM.email == email))
        if existing.scalar_one_or_none():
            raise ValueError("Ese email ya está registrado")

        base_slug = _slugify(org_name)
        slug = base_slug
        n = 1
        while True:
            hit = await self._session.execute(select(OrganizationORM).where(OrganizationORM.slug == slug))
            if not hit.scalar_one_or_none():
                break
            n += 1
            slug = f"{base_slug}-{n}"

        org = OrganizationORM(id=str(uuid.uuid4()), name=org_name.strip()[:160], slug=slug)
        user = UserORM(
            id=str(uuid.uuid4()),
            org_id=org.id,
            email=email,
            password_hash=_hash_password(password),
            full_name=(full_name or org_name)[:160],
            role=role if role in ("desk", "company_admin", "viewer") else "company_admin",
        )
        self._session.add(org)
        self._session.add(user)
        await self._session.commit()
        token = await self._issue_session(user)
        return org, user, token

    async def login_email(self, email: str, password: str) -> dict[str, Any]:
        email = (email or "").strip().lower()
        result = await self._session.execute(select(UserORM).where(UserORM.email == email))
        user = result.scalar_one_or_none()
        if not user or not user.active or not _verify_password(password, user.password_hash):
            raise ValueError("Email o contraseña incorrectos")

        org_r = await self._session.execute(
            select(OrganizationORM).where(OrganizationORM.id == user.org_id)
        )
        org = org_r.scalar_one_or_none()
        if not org or not org.active:
            raise ValueError("Empresa inactiva")

        user.last_login_at = utc_now()
        await self._session.commit()
        token = await self._issue_session(user)
        return {
            "ok": True,
            "token": token,
            "auth_type": "session",
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
            },
            "organization": {"id": org.id, "name": org.name, "slug": org.slug},
        }

    async def login_desk_token(self, token: str) -> dict[str, Any]:
        settings = get_settings()
        if not settings.dashboard_access_token:
            raise ValueError("Auth de mesa no configurado")
        if not hmac.compare_digest(token or "", settings.dashboard_access_token):
            raise ValueError("Token de mesa incorrecto")
        return {
            "ok": True,
            "token": token,
            "auth_type": "desk",
            "user": {"id": "desk", "email": "desk@monarch", "full_name": "Mesa Monarch", "role": "desk"},
            "organization": {"id": "monarch", "name": "Monarch Capital", "slug": "monarch"},
        }

    async def resolve_bearer(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        settings = get_settings()
        if settings.dashboard_access_token and hmac.compare_digest(token, settings.dashboard_access_token):
            phone, has_key = "", False
            try:
                prefs = await self.get_notify_prefs(user_id="desk", role="desk")
                phone = prefs.get("notify_phone") or ""
                has_key = bool(prefs.get("has_whatsapp_key"))
            except Exception:
                pass
            return {
                "auth_type": "desk",
                "role": "desk",
                "user_id": "desk",
                "org_id": "monarch",
                "email": "desk@monarch",
                "notify_phone": phone,
                "has_whatsapp_key": has_key,
            }

        th = _hash_token(token)
        result = await self._session.execute(
            select(AuthSessionORM).where(
                AuthSessionORM.token_hash == th,
                AuthSessionORM.revoked.is_(False),
            )
        )
        sess = result.scalar_one_or_none()
        if not sess:
            return None
        exp = sess.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
        user_r = await self._session.execute(select(UserORM).where(UserORM.id == sess.user_id))
        user = user_r.scalar_one_or_none()
        if not user or not user.active:
            return None
        return {
            "auth_type": "session",
            "role": user.role,
            "user_id": user.id,
            "org_id": user.org_id,
            "email": user.email,
            "notify_phone": getattr(user, "notify_phone", None) or "",
            "has_whatsapp_key": bool(getattr(user, "notify_whatsapp_key", None)),
        }

    async def revoke_token(self, token: str) -> None:
        th = _hash_token(token)
        result = await self._session.execute(select(AuthSessionORM).where(AuthSessionORM.token_hash == th))
        sess = result.scalar_one_or_none()
        if sess:
            sess.revoked = True
            await self._session.commit()

    async def _issue_session(self, user: UserORM) -> str:
        raw = secrets.token_urlsafe(32)
        sess = AuthSessionORM(
            id=str(uuid.uuid4()),
            user_id=user.id,
            org_id=user.org_id,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(days=_SESSION_DAYS),
        )
        self._session.add(sess)
        await self._session.commit()
        return raw

    @staticmethod
    def normalize_phone(phone: str | None) -> str | None:
        """Return E.164-ish digits (optional leading +) or None if clearing/invalid."""
        raw = (phone or "").strip()
        if not raw:
            return None
        # Keep leading + then digits only
        has_plus = raw.startswith("+")
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8 or len(digits) > 15:
            raise ValueError("Número inválido. Usa código de país, ej. +573001234567")
        return ("+" if has_plus else "") + digits

    async def update_notify_prefs(
        self,
        *,
        user_id: str | None,
        org_id: str | None,
        role: str,
        phone: str | None,
        whatsapp_api_key: str | None = None,
        clear_whatsapp_key: bool = False,
    ) -> dict[str, Any]:
        """Save WhatsApp number for the logged-in user (or desk org inbox)."""
        normalized = self.normalize_phone(phone)

        if role == "desk" or user_id in (None, "desk"):
            org = await self._ensure_desk_org()
            org.notify_phone = normalized
            if clear_whatsapp_key:
                org.notify_whatsapp_key = None
            elif whatsapp_api_key is not None and whatsapp_api_key.strip():
                org.notify_whatsapp_key = whatsapp_api_key.strip()[:128]
            await self._session.commit()
            return {
                "ok": True,
                "notify_phone": org.notify_phone or "",
                "has_whatsapp_key": bool(org.notify_whatsapp_key),
                "scope": "desk",
            }

        if not user_id:
            raise ValueError("Sesión sin usuario")
        result = await self._session.execute(select(UserORM).where(UserORM.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.active:
            raise ValueError("Usuario no encontrado")
        if org_id and user.org_id != org_id:
            raise ValueError("Sesión inválida")

        user.notify_phone = normalized
        if clear_whatsapp_key:
            user.notify_whatsapp_key = None
        elif whatsapp_api_key is not None and whatsapp_api_key.strip():
            user.notify_whatsapp_key = whatsapp_api_key.strip()[:128]

        # Also stamp company inbox so all org alerts reach this number
        org_r = await self._session.execute(
            select(OrganizationORM).where(OrganizationORM.id == user.org_id)
        )
        org = org_r.scalar_one_or_none()
        if org and user.role in ("company_admin", "desk"):
            org.notify_phone = normalized
            if clear_whatsapp_key:
                org.notify_whatsapp_key = None
            elif whatsapp_api_key is not None and whatsapp_api_key.strip():
                org.notify_whatsapp_key = whatsapp_api_key.strip()[:128]

        await self._session.commit()
        return {
            "ok": True,
            "notify_phone": user.notify_phone or "",
            "has_whatsapp_key": bool(user.notify_whatsapp_key),
            "scope": "user",
        }

    async def _ensure_desk_org(self) -> OrganizationORM:
        result = await self._session.execute(
            select(OrganizationORM).where(OrganizationORM.id == "monarch")
        )
        org = result.scalar_one_or_none()
        if org:
            return org
        org = OrganizationORM(
            id="monarch",
            name="Monarch Capital",
            slug="monarch",
            active=True,
        )
        self._session.add(org)
        await self._session.commit()
        await self._session.refresh(org)
        return org

    async def list_notify_targets(self, org_id: str | None) -> list[dict[str, str | None]]:
        """Phones that should receive alerts for this org (users + org inbox)."""
        if not org_id:
            return []
        targets: list[dict[str, str | None]] = []
        seen: set[str] = set()

        def _add(phone: str | None, key: str | None) -> None:
            if not phone:
                return
            digits = re.sub(r"\D", "", phone)
            if not digits or digits in seen:
                return
            seen.add(digits)
            targets.append({"phone": phone, "whatsapp_api_key": key})

        users_r = await self._session.execute(
            select(UserORM).where(UserORM.org_id == org_id, UserORM.active.is_(True))
        )
        for u in users_r.scalars().all():
            _add(getattr(u, "notify_phone", None), getattr(u, "notify_whatsapp_key", None))

        org_r = await self._session.execute(
            select(OrganizationORM).where(OrganizationORM.id == org_id)
        )
        org = org_r.scalar_one_or_none()
        if org:
            _add(getattr(org, "notify_phone", None), getattr(org, "notify_whatsapp_key", None))

        return targets

    async def get_notify_prefs(self, *, user_id: str | None, role: str) -> dict[str, Any]:
        if role == "desk" or user_id in (None, "desk"):
            org = await self._ensure_desk_org()
            return {
                "notify_phone": org.notify_phone or "",
                "has_whatsapp_key": bool(org.notify_whatsapp_key),
                "scope": "desk",
            }
        if not user_id:
            return {"notify_phone": "", "has_whatsapp_key": False, "scope": "none"}
        result = await self._session.execute(select(UserORM).where(UserORM.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return {"notify_phone": "", "has_whatsapp_key": False, "scope": "none"}
        return {
            "notify_phone": user.notify_phone or "",
            "has_whatsapp_key": bool(user.notify_whatsapp_key),
            "scope": "user",
        }

    async def list_companies(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        total_r = await self._session.execute(select(func.count()).select_from(OrganizationORM))
        total = int(total_r.scalar() or 0)
        r = await self._session.execute(
            select(OrganizationORM)
            .order_by(OrganizationORM.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        orgs = [
            {
                "id": o.id,
                "name": o.name,
                "slug": o.slug,
                "active": o.active,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in r.scalars().all()
        ]
        return orgs, total
