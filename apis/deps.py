"""FastAPI dependencies — auth principal and org scope."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class OrgScope:
    """Tenant scope derived from the authenticated principal."""

    role: str
    org_id: str | None
    user_id: str | None
    email: str | None
    auth_type: str | None

    @property
    def is_desk(self) -> bool:
        return self.role == "desk"

    def read_org_id(self) -> str | None:
        """Filter key for reads. None = desk sees everything."""
        if self.is_desk:
            return None
        return self.org_id

    def write_org_id(self) -> str:
        """Stamp on creates. Desk data lands under 'monarch'."""
        if self.is_desk:
            return "monarch"
        if not self.org_id:
            raise HTTPException(status_code=403, detail="Sesión sin empresa")
        return self.org_id

    def book_org_id(self) -> str:
        """Operational book for discovery/watchlist/alerts flows (never cross-tenant)."""
        return self.write_org_id()


def get_optional_principal(request: Request) -> dict | None:
    return getattr(request.state, "principal", None)


async def get_principal(request: Request) -> dict:
    p = get_optional_principal(request)
    if not p:
        raise HTTPException(status_code=401, detail="No autenticado")
    return p


async def get_org_scope(request: Request) -> OrgScope:
    p = get_optional_principal(request)
    if not p:
        # Dev / auth-off: treat as desk so existing flows keep working
        return OrgScope(role="desk", org_id="monarch", user_id="anon", email=None, auth_type=None)
    return OrgScope(
        role=str(p.get("role") or "viewer"),
        org_id=p.get("org_id"),
        user_id=p.get("user_id"),
        email=p.get("email"),
        auth_type=p.get("auth_type"),
    )
