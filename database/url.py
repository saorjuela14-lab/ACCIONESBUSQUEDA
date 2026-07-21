"""Database URL helpers — SQLite (local/ephemeral) and Postgres (persistent)."""

from __future__ import annotations


def normalize_database_url(url: str) -> str:
    """Normalize provider URLs to SQLAlchemy async drivers.

    Accepts common Neon/Railway/Supabase forms:
    - postgres://... → postgresql+asyncpg://...
    - postgresql://... → postgresql+asyncpg://...
    Leaves sqlite+aiosqlite:// unchanged.
    """
    raw = (url or "").strip()
    if not raw:
        return "sqlite+aiosqlite:///./data/nexbuy.db"

    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]

    if raw.startswith("postgresql+asyncpg://"):
        return raw

    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://") :]

    return raw


def is_sqlite(url: str) -> bool:
    return "sqlite" in (url or "").lower()


def is_postgres(url: str) -> bool:
    u = (url or "").lower()
    return "postgresql" in u or u.startswith("postgres://")
