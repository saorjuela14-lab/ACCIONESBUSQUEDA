"""Alpaca paper broker factory for multi-asset beta (isolated from firm LIVE)."""

from __future__ import annotations

from config.settings import get_settings
from providers.broker.alpaca_provider import PAPER_BASE_URL, AlpacaBrokerProvider
from utils.logging import get_logger

logger = get_logger(__name__)


def get_beta_broker_provider() -> AlpacaBrokerProvider:
    """Always paper. Prefer dedicated beta keys; else reuse firm keys only if firm is paper."""
    settings = get_settings()
    key = (settings.alpaca_beta_api_key or "").strip()
    secret = (settings.alpaca_beta_secret_key or "").strip()
    reused = False
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
    base = (settings.alpaca_beta_base_url or PAPER_BASE_URL).rstrip("/")
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
    )
    return broker
