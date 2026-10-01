"""Database URL helpers — SQLite (local/ephemeral) and Postgres (persistent)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_CRED_IN_URL = re.compile(r"//[^/\s]+:[^/\s]+@")

# Neon project curly-surf-33371253 / endpoint ep-small-pond-awyhvhoz
NEON_DIRECT_HOST = "ep-small-pond-awyhvhoz.c-12.us-east-1.aws.neon.tech"
NEON_POOLER_HOST = "ep-small-pond-awyhvhoz-pooler.c-12.us-east-1.aws.neon.tech"


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


def database_host(url: str) -> str | None:
    """Hostname only — never user/password. Used for logs and health."""
    raw = normalize_database_url(url)
    if is_sqlite(raw):
        return None
    host = urlparse(raw).hostname
    return host or None


def sanitize_db_error(exc: BaseException) -> str:
    """Log/health text without connection-string credentials."""
    text = f"{type(exc).__name__}: {exc}"
    text = _CRED_IN_URL.sub("//***:***@", text)
    return text[:240]
