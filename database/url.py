"""Database URL helpers — SQLite (local/ephemeral) and Postgres (persistent)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def normalize_database_url(url: str) -> str:
    """Normalize provider URLs to SQLAlchemy async drivers.

    Accepts common Neon/Railway/Supabase forms:
    - postgres://... → postgresql+asyncpg://...
    - postgresql://... → postgresql+asyncpg://...
    - sslmode=require → ssl=require (asyncpg-compatible)
    Leaves sqlite+aiosqlite:// unchanged.
    """
    raw = (url or "").strip()
    if not raw:
        return "sqlite+aiosqlite:///./data/nexbuy.db"

    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]

    if raw.startswith("postgresql://"):
        raw = "postgresql+asyncpg://" + raw[len("postgresql://") :]

    if not raw.startswith("postgresql+asyncpg://"):
        return raw

    parsed = urlparse(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # asyncpg rejects libpq's sslmode=; map to ssl=
    if "sslmode" in query:
        mode = query.pop("sslmode")
        if mode and mode.lower() not in ("disable", "allow", "prefer"):
            query.setdefault("ssl", "require")
    query.pop("channel_binding", None)
    return urlunparse(parsed._replace(query=urlencode(query)))


def is_sqlite(url: str) -> bool:
    return "sqlite" in (url or "").lower()


def is_postgres(url: str) -> bool:
    u = (url or "").lower()
    return "postgresql" in u or u.startswith("postgres://")
