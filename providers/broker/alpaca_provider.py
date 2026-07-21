"""Alpaca Trading API broker provider (paper + live).

Uses REST endpoints documented at https://docs.alpaca.markets/
Auth headers: APCA-API-KEY-ID / APCA-API-SECRET-KEY
Persists X-Request-ID from responses for support.
"""

from __future__ import annotations

from typing import Any

import httpx

from providers.interfaces import BrokerProvider
from utils.logging import get_logger

logger = get_logger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"


class AlpacaBrokerProvider(BrokerProvider):
    """Thin async client for Alpaca Trading API v2."""

    name = "alpaca"

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        base_url: str | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._secret_key = (secret_key or "").strip()
        self._paper = paper
        if base_url:
            self._base_url = base_url.rstrip("/")
        else:
            self._base_url = PAPER_BASE_URL if paper else LIVE_BASE_URL
        self.last_request_id: str | None = None

    def is_configured(self) -> bool:
        return bool(self._api_key and self._secret_key)

    @property
    def paper(self) -> bool:
        return self._paper

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _capture_request_id(self, response: httpx.Response) -> None:
        rid = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
        if rid:
            self.last_request_id = rid

    def _raise_for_alpaca(self, response: httpx.Response) -> None:
        self._capture_request_id(response)
        if response.is_success:
            return
        detail = ""
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or str(body)
        except Exception:
            detail = response.text[:300]
        rid = self.last_request_id or "n/a"
        raise httpx.HTTPStatusError(
            f"Alpaca {response.status_code}: {detail} (X-Request-ID: {rid})",
            request=response.request,
            response=response,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.is_configured():
            raise ValueError(
                "Alpaca no configurada. Define ALPACA_API_KEY y ALPACA_SECRET_KEY "
                "(mismas vars que https://github.com/alpacahq/cli)."
            )

        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
            )
            self._raise_for_alpaca(response)
            if response.status_code == 204 or not response.content:
                return {"ok": True, "request_id": self.last_request_id}
            data = response.json()
            if isinstance(data, dict):
                data["_request_id"] = self.last_request_id
            return data

    async def get_account(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/account")

    async def get_positions(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v2/positions")
        return data if isinstance(data, list) else []

    async def list_orders(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/v2/orders",
            params={"status": status, "limit": limit, "direction": "desc"},
        )
        return data if isinstance(data, list) else []

    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        logger.info(
            "alpaca.submit_order",
            symbol=order.get("symbol"),
            qty=order.get("qty"),
            side=order.get("side"),
            type=order.get("type"),
            paper=self._paper,
        )
        result = await self._request("POST", "/v2/orders", json_body=order)
        if isinstance(result, dict):
            return result
        return {"raw": result, "_request_id": self.last_request_id}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v2/orders/{order_id}")

    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        """DELETE /v2/orders — cancels every open order (CLI: alpaca order cancel-all)."""
        data = await self._request("DELETE", "/v2/orders")
        if isinstance(data, list):
            return data
        return [data] if data else []

    async def close_position(self, symbol: str) -> dict[str, Any]:
        """DELETE /v2/positions/{symbol} — liquidate one position."""
        return await self._request("DELETE", f"/v2/positions/{symbol.upper()}")

    async def close_all_positions(self, *, cancel_orders: bool = True) -> list[dict[str, Any]]:
        """DELETE /v2/positions — liquidate entire portfolio (CLI: alpaca position close-all)."""
        data = await self._request(
            "DELETE",
            "/v2/positions",
            params={"cancel_orders": str(cancel_orders).lower()},
        )
        if isinstance(data, list):
            return data
        return [data] if data else []

    async def get_clock(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/clock")
