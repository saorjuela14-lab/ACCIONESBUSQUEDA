"""FastAPI dependencies — auth principal and org scope."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class OrgScope:
    """Tenant scope derived from the authenticated principal.

    Clients (approved companies) only *monitor* the firm desk book (`monarch`).
    Only the mesa (`desk`) can invest / mutate capital.
    """

    role: str
    org_id: str | None
    user_id: str | None
    email: str | None
    auth_type: str | None

    @property
    def is_desk(self) -> bool:
        return self.role == "desk"

    @property
    def is_client(self) -> bool:
        return not self.is_desk

    def read_org_id(self) -> str | None:
        """Filter key for reads. None = desk admin sees every tenant row."""
        if self.is_desk:
            return None
        # Approved clients monitor the shared Monarch desk account only
        return "monarch"

    def write_org_id(self) -> str:
        """Stamp on creates. Only the mesa may mutate the investment book."""
        if self.is_desk:
            return "monarch"
        raise HTTPException(
            status_code=403,
            detail="Solo la mesa Monarch puede invertir o modificar la cuenta",
        )

    def book_org_id(self) -> str:
        """Operational book shown in the terminal (always the firm desk book)."""
        return "monarch"

    def require_desk(self) -> None:
        if not self.is_desk:
            raise HTTPException(
                status_code=403,
                detail="Solo la mesa Monarch puede ejecutar esta acción",
            )


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
