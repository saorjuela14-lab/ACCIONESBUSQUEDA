"""Provider connectivity diagnostics."""

from typing import Any

import httpx

from config.settings import get_settings
from providers.macro.factory import get_macro_provider
from providers.market.factory import get_market_provider


async def check_polygon_key(api_key: str, base_url: str) -> dict[str, Any]:
    """Test Polygon/Massive API key against a lightweight endpoint."""
    path = "/v3/reference/tickers/AAPL"
    results: dict[str, Any] = {"configured": bool(api_key), "authenticated": False, "base_url": base_url}

    if not api_key:
        results["message"] = "POLYGON_API_KEY no configurada en .env"
        return results

    async with httpx.AsyncClient(timeout=15.0) as client:
        for mode in ("bearer", "query"):
            try:
                if mode == "bearer":
                    response = await client.get(
                        f"{base_url.rstrip('/')}{path}",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                else:
                    response = await client.get(
                        f"{base_url.rstrip('/')}{path}",
                        params={"apiKey": api_key},
                    )
                results[f"auth_{mode}"] = response.status_code
                if response.status_code == 200:
                    results["authenticated"] = True
                    results["message"] = "API key válida y autenticada"
                    return results
                if response.status_code == 403:
                    results["message"] = "Key válida pero sin permiso para este endpoint (revisa tu plan)"
                    return results
            except Exception as exc:
                results[f"auth_{mode}_error"] = str(exc)

    results["message"] = (
        "401 Unauthorized: la key no es aceptada. Verifica en massive.com/dashboard/api-keys "
        "que copiaste la key correcta y que tu plan está activo."
    )
    return results


async def get_providers_status() -> dict[str, Any]:
    settings = get_settings()

    polygon = await check_polygon_key(settings.polygon_api_key, settings.polygon_api_base_url)

    return {
        "system_note": (
            "System Status UP en Massive = servidores funcionando. "
            "Stocks/Equities CLOSED = mercado fuera de horario (normal). "
            "Esto NO bloquea datos históricos ni /prev. Un 401 es problema de API key, no de mercado cerrado."
        ),
        "market_hours": {
            "stocks_us": "closed_outside_0930_1600_ET",
            "note": "Mercado cerrado permite consultar datos históricos y cierre anterior",
        },
        "providers": {
            "polygon": polygon,
            "alpha_vantage": {
                "configured": bool(settings.alpha_vantage_api_key),
                "daily_limit": settings.alpha_vantage_daily_limit,
            },
            "fred": {"configured": bool(settings.fred_api_key)},
            "yfinance": {"enabled": settings.yfinance_enabled},
            "alpaca": {
                "configured": bool(settings.alpaca_api_key and settings.alpaca_secret_key),
                "paper": settings.alpaca_paper,
                "trading_base_url": settings.alpaca_base_url
                or ("https://paper-api.alpaca.markets" if settings.alpaca_paper else "https://api.alpaca.markets"),
                "data_base_url": settings.alpaca_data_base_url or "https://data.alpaca.markets",
                "data_feed": settings.alpaca_data_feed,
            },
        },
        "fallback_chain": ["alpaca", "polygon", "alpha_vantage", "yfinance"],
        "usage": _safe_usage_stats(),
    }


def _safe_usage_stats() -> dict[str, Any]:
    try:
        provider = get_market_provider()
        if hasattr(provider, "usage_stats"):
            return provider.usage_stats()
    except Exception:
        pass
    return {}
