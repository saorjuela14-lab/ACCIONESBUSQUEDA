"""Broker provider factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.settings import get_settings

if TYPE_CHECKING:
    from providers.broker.alpaca_provider import AlpacaBrokerProvider


def get_broker_provider() -> AlpacaBrokerProvider:
    from providers.broker.alpaca_provider import AlpacaBrokerProvider
    from services.live_safety import production_trading_unconfigured

    settings = get_settings()
    if production_trading_unconfigured(settings):
        # Empty keys: no HTTP. Do not guess paper vs LIVE (LIVE keys on paper-api).
        return AlpacaBrokerProvider(api_key="", secret_key="", paper=True)
    return AlpacaBrokerProvider(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.effective_alpaca_paper,
        base_url=settings.alpaca_base_url or None,
    )
