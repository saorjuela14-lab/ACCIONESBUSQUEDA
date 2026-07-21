"""Broker provider factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.settings import get_settings

if TYPE_CHECKING:
    from providers.broker.alpaca_provider import AlpacaBrokerProvider


def get_broker_provider() -> AlpacaBrokerProvider:
    from providers.broker.alpaca_provider import AlpacaBrokerProvider

    settings = get_settings()
    return AlpacaBrokerProvider(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.effective_alpaca_paper,
        base_url=settings.alpaca_base_url or None,
    )
