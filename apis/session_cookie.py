"""Cookie-safe session values for the httponly nexbuy_token cookie.

The desk token may contain '@' (Starlette then quotes the cookie; some Chrome/Edge
and installed PWAs drop non-Secure or quoted cookies). Encode so Set-Cookie is
alphanumeric, and mark Secure whenever the request is HTTPS — even if APP_ENV
is still 'development' on FastAPI Cloud.
"""

from __future__ import annotations

import base64

from starlette.requests import Request

from config.settings import get_settings

COOKIE_PREFIX = "m1."


def encode_session_cookie(token: str) -> str:
    packed = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{COOKIE_PREFIX}{packed}"


def decode_session_cookie(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().strip('"')
    if raw.startswith(COOKIE_PREFIX):
        blob = raw[len(COOKIE_PREFIX) :]
        pad = "=" * ((4 - len(blob) % 4) % 4)
        try:
            return base64.urlsafe_b64decode(blob + pad).decode("utf-8")
        except Exception:
            return None
    return raw


def cookie_should_be_secure(request: Request | None) -> bool:
    if get_settings().app_env == "production":
        return True
    if request is None:
        return False
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "")
    proto = proto.split(",")[0].strip().lower()
    return proto == "https"
