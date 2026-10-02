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
        self.last_next_page_token: str | None = None

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
        code = None
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or str(body)
            code = body.get("code")
        except Exception:
            detail = response.text[:300]
        rid = self.last_request_id or "n/a"
        err = httpx.HTTPStatusError(
            f"Alpaca {response.status_code}: {detail} (X-Request-ID: {rid})",
            request=response.request,
            response=response,
        )
        err.alpaca_code = code
        err.alpaca_status = response.status_code
        raise err

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
            self.last_next_page_token = (
                response.headers.get("next-page-token")
                or response.headers.get("x-next-page-token")
                or None
            )
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

    async def list_orders(
        self, status: str = "all", limit: int = 50, page_token: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
            "direction": "desc",
            "nested": "true",
        }
        if page_token:
            params["page_token"] = str(page_token)
        data = await self._request("GET", "/v2/orders", params=params)
        rows = data if isinstance(data, list) else []
        flat = flatten_orders_with_legs(rows)
        if not self.last_next_page_token and len(rows) >= int(limit or 0) and rows:
            last = rows[-1] if isinstance(rows[-1], dict) else {}
            self.last_next_page_token = str(last.get("id") or "") or None
        return flat

    async def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        """GET /v2/orders:by_client_order_id/{client_order_id}. None if missing."""
        cid = (client_order_id or "").strip()
        if not cid:
            return None
        from urllib.parse import quote

        path = f"/v2/orders:by_client_order_id/{quote(cid, safe='')}"
        try:
            data = await self._request("GET", path)
        except httpx.HTTPStatusError as exc:
            status = getattr(exc, "alpaca_status", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            if int(status or 0) == 404:
                return None
            raise
        return data if isinstance(data, dict) else None

    async def replace_order(
        self,
        order_id: str,
        *,
        stop_price: float | None = None,
        qty: float | None = None,
        time_in_force: str | None = None,
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        """PATCH /v2/orders/{id} — mutate an existing working order (bracket stop leg)."""
        oid = (order_id or "").strip()
        if not oid:
            raise ValueError("order_id requerido para replace")
        body: dict[str, Any] = {}
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if qty is not None:
            body["qty"] = str(qty)
        if time_in_force:
            body["time_in_force"] = time_in_force
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if not body:
            raise ValueError("replace_order requiere al menos un campo")
        logger.info("alpaca.replace_order", order_id=oid, fields=list(body.keys()), paper=self._paper)
        result = await self._request("PATCH", f"/v2/orders/{oid}", json_body=body)
        if isinstance(result, dict):
            return result
        return {"raw": result, "_request_id": self.last_request_id}

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
        """DELETE /v2/positions/{symbol} — liquidate one position.

        Alpaca cancels open orders tied to that symbol as part of the close.
        If this call fails, we do not cancel brackets ourselves (stop stays).
        """
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

    async def get_asset(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/assets/{symbol.upper()}")

    async def list_crypto_assets(self) -> list[dict[str, Any]]:
        """GET /v2/assets?asset_class=crypto&status=active"""
        data = await self._request(
            "GET",
            "/v2/assets",
            params={"asset_class": "crypto", "status": "active"},
        )
        return data if isinstance(data, list) else []

    async def get_clock(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/clock")

    async def list_account_activities(
        self,
        *,
        activity_types: str | list[str] | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        direction: str = "desc",
        category: str | None = None,
        after: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /v2/account/activities or /v2/account/activities/{type}."""
        params: dict[str, Any] = {
            "page_size": max(1, min(int(page_size), 100)),
            "direction": direction or "desc",
        }
        if category:
            params["category"] = category
        path = "/v2/account/activities"
        if activity_types:
            if isinstance(activity_types, (list, tuple)):
                types = [str(t).strip().upper() for t in activity_types if t]
            else:
                types = [t.strip().upper() for t in str(activity_types).split(",") if t.strip()]
            if len(types) == 1:
                path = f"/v2/account/activities/{types[0]}"
            elif types:
                params["activity_types"] = ",".join(types)
        if page_token:
            params["page_token"] = str(page_token)
        if after:
            params["after"] = str(after)
        if until:
            params["until"] = str(until)
        data = await self._request("GET", path, params=params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = data.get("activities") or data.get("data") or []
            return inner if isinstance(inner, list) else []
        return []


def flatten_orders_with_legs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand nested bracket/OTO legs so stop legs are visible as orders."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("id") or "")
        if oid and oid in seen:
            continue
        if oid:
            seen.add(oid)
        out.append(raw)
        for leg in raw.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            lid = str(leg.get("id") or "")
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            out.append(leg)
    return out

