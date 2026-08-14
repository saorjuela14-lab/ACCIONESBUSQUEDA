"""Database URL helpers — SQLite (local/ephemeral) and Postgres (persistent)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_QUOTE_CHARS = "'\"“”‘’«»"


def clean_database_url_input(url: str) -> str:
    """Strip quotes/whitespace/prefixes that break SQLAlchemy parsing in cloud envs."""
    raw = (url or "").strip().lstrip("\ufeff")
    # FastAPI Cloud / dashboards often wrap secrets in quotes (incl. curly/smart quotes)
    if len(raw) >= 2 and raw[0] in _QUOTE_CHARS and raw[-1] in _QUOTE_CHARS:
        raw = raw[1:-1].strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    for prefix in ("DATABASE_URL=", "database_url=", "DATABASE_URL:", "database_url:"):
        if raw.lower().startswith(prefix.lower()):
            raw = raw[len(prefix) :].strip()
            if len(raw) >= 2 and raw[0] in _QUOTE_CHARS and raw[-1] in _QUOTE_CHARS:
                raw = raw[1:-1].strip()
            break
    # Neon copy/paste sometimes includes wrapping spaces or newlines
    raw = raw.replace("\n", "").replace("\r", "").replace("\t", "").strip()
    return raw


def normalize_database_url(url: str) -> str:
    """Normalize provider URLs to SQLAlchemy async drivers.

    Accepts common Neon/Railway/Supabase forms:
    - postgres://... → postgresql+asyncpg://...
    - postgresql://... → postgresql+asyncpg://...
    - sslmode=require → ssl=require (asyncpg-compatible)
    Leaves sqlite+aiosqlite:// unchanged.
    """
    raw = clean_database_url_input(url)
    if not raw:
        return "sqlite+aiosqlite:///./data/nexbuy.db"

    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]

    if raw.startswith("postgresql://"):
        raw = "postgresql+asyncpg://" + raw[len("postgresql://") :]

    if not raw.startswith("postgresql+asyncpg://") and not raw.startswith("sqlite"):
        # Last resort: if it looks like a Neon host without scheme
        if "neon.tech" in raw and "://" not in raw:
            raw = "postgresql+asyncpg://" + raw

    if not raw.startswith("postgresql+asyncpg://"):
        return raw

    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError(
            "DATABASE_URL inválida (sin host). En FastAPI Cloud pega solo la URL, "
            "sin comillas ni el texto DATABASE_URL=."
        )

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # asyncpg rejects libpq's sslmode=; map to ssl=
    if "sslmode" in query:
        mode = query.pop("sslmode")
        if mode and mode.lower() not in ("disable", "allow", "prefer"):
            query.setdefault("ssl", "require")
    query.pop("channel_binding", None)
    # Neon usually needs SSL
    if "neon.tech" in (parsed.hostname or "") and "ssl" not in query:
        query["ssl"] = "require"
    return urlunparse(parsed._replace(query=urlencode(query)))


def redact_database_url(url: str) -> str:
    """Safe-to-log form of a DB URL (password hidden)."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            return "<invalid-url>"
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            netloc = f"{parsed.username}:***@{netloc}"
        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return "<unparseable-url>"


def is_sqlite(url: str) -> bool:
    return "sqlite" in (url or "").lower()


def is_postgres(url: str) -> bool:
    u = (url or "").lower()
    return "postgresql" in u or u.startswith("postgres://")
