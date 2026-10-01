"""Alpaca paper broker factory for multi-asset beta (isolated from firm LIVE)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from config.settings import get_settings
from providers.broker.alpaca_provider import PAPER_BASE_URL, AlpacaBrokerProvider
from utils.logging import get_logger

logger = get_logger(__name__)

PAPER_HOST = "paper-api.alpaca.markets"


class MultiAssetNotPaperError(RuntimeError):
    """Raised when Multi-Asset would talk to anything other than Alpaca paper."""


def beta_base_url_is_paper(url: Any) -> bool:
    if not isinstance(url, str) or not url.strip():
        url = PAPER_BASE_URL
    raw = url.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    return host == PAPER_HOST


def assert_beta_url_is_paper(url: Any = None) -> str:
    """Refuse any ALPACA_BETA_BASE_URL that is not paper-api.alpaca.markets."""
    settings = get_settings()
    raw = url if isinstance(url, str) and url.strip() else (settings.alpaca_beta_base_url or PAPER_BASE_URL)
    if not beta_base_url_is_paper(raw):
        raise MultiAssetNotPaperError(
            "Multi-Asset solo opera en Alpaca PAPER. "
            f"ALPACA_BETA_BASE_URL debe ser https://{PAPER_HOST} (recibido: {raw!r})."
        )
    return str(raw).rstrip("/")


async def assert_beta_account_is_paper(broker: Any) -> None:
    """URL must be paper; if the account payload says paper:false, refuse orders."""
    base = getattr(broker, "base_url", None)
    assert_beta_url_is_paper(base if isinstance(base, str) else None)
    if broker is None or not getattr(broker, "is_configured", lambda: False)():
        return
    get_acct = getattr(broker, "get_account", None)
    if get_acct is None:
        return
    acct = await get_acct()
    if not isinstance(acct, dict):
        return
    if acct.get("paper") is False:
        raise MultiAssetNotPaperError(
            "Multi-Asset abortado: la cuenta Alpaca conectada responde paper=false (LIVE). "
            "No se envían órdenes."
        )


def get_beta_broker_provider() -> AlpacaBrokerProvider:
    """Always paper. Prefer dedicated beta keys; else reuse firm keys only if firm is paper."""
    settings = get_settings()
    base = assert_beta_url_is_paper(settings.alpaca_beta_base_url or PAPER_BASE_URL)
    key = (settings.alpaca_beta_api_key or "").strip()
    secret = (settings.alpaca_beta_secret_key or "").strip()
    reused = False
    if key and secret and key == secret:
        logger.error(
            "multiasset.beta.same_key_and_secret",
            hint=(
                "ALPACA_BETA_API_KEY y ALPACA_BETA_SECRET_KEY deben ser distintos. "
                "En Alpaca Paper → API Keys verás Key ID (PK…) y Secret Key (otra cadena)."
            ),
        )
        # Treat as unconfigured so UI shows a clear message instead of opaque 401s
        key, secret = "", ""
    if not key or not secret:
        if settings.effective_alpaca_paper and settings.alpaca_api_key and settings.alpaca_secret_key:
            key = settings.alpaca_api_key
            secret = settings.alpaca_secret_key
            reused = True
        else:
            logger.warning(
                "multiasset.beta.broker_unconfigured",
                hint="Define ALPACA_BETA_API_KEY / ALPACA_BETA_SECRET_KEY (paper)",
            )
    broker = AlpacaBrokerProvider(
        api_key=key,
        secret_key=secret,
        paper=True,
        base_url=base,
    )
    logger.info(
        "multiasset.beta.broker",
        configured=broker.is_configured(),
        reused_firm_paper_keys=reused,
        base_url=base,
        key_len=len(key),
        secret_len=len(secret),
        keys_equal=bool(key) and key == secret,
    )
    return broker
