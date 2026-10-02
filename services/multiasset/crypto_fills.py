"""Resolve paper crypto fills from the live order + CFEE (qty in coin)."""

from __future__ import annotations

from typing import Any

from services.multiasset.crypto_risk import alpaca_min_qty

FILLED_STATUSES = frozenset({"filled", "partially_filled"})
BUY_NET_QTY_RATE = 0.9975  # 0.25% CFEE in coin — configurable, default official
POSITION_DELTA_ALERT_REL = 1e-6
DEAD_NO_FILL = frozenset(
    {"canceled", "cancelled", "expired", "rejected", "done_for_day"}
)
DUST_NOTIONAL_USD = 1.0


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(symbol: str) -> str:
    return (symbol or "").upper().replace("/", "").replace("-", "")


def buy_net_qty(filled_qty: float, *, rate: float | None = None) -> float:
    """Buy net = filled_qty × rate (default 0.9975). Sells do not change qty."""
    r = BUY_NET_QTY_RATE if rate is None else float(rate)
    return float(filled_qty or 0) * r


def position_qty_delta_alert(
    net_qty: float,
    position_qty: float | None,
    *,
    rel: float = POSITION_DELTA_ALERT_REL,
) -> dict[str, Any] | None:
    if position_qty is None:
        return None
    try:
        pos = float(position_qty)
    except (TypeError, ValueError):
        return None
    denom = max(abs(pos), 1e-12)
    rel_diff = abs(float(net_qty) - pos) / denom
    if rel_diff > float(rel):
        return {
            "alert": True,
            "net_qty": float(net_qty),
            "position_qty": pos,
            "rel_diff": rel_diff,
        }
    return None


def net_qty_from_fill_and_cfee(
    *,
    filled_qty: float,
    cfee_qty: float | None = None,
) -> float:
    """Reconcile helper: fill qty minus coin fee (CFEE qty is negative on Alpaca)."""
    fill = float(filled_qty or 0)
    fee = float(cfee_qty or 0)
    if fee > 0:
        fee = -fee
    return fill + fee


def is_dust(qty: float, price: float, *, symbol: str | None = None) -> bool:
    q = float(qty or 0)
    px = float(price or 0)
    if q <= 1e-12:
        return True
    floor = alpaca_min_qty(symbol or "") if symbol else None
    if floor is not None and q + 1e-12 < float(floor):
        return True
    if px > 0 and q * px < DUST_NOTIONAL_USD:
        return True
    return False


def cfee_qty_for_symbol(activities: list[dict[str, Any]] | None, symbol: str) -> float:
    want = _norm(symbol)
    total = 0.0
    for row in activities or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("activity_type") or row.get("type") or "").upper() != "CFEE":
            continue
        if _norm(str(row.get("symbol") or "")) != want:
            continue
        q = _f(row.get("qty"))
        if q is None:
            continue
        total += q if q < 0 else -abs(q)
    return total


def order_is_filled(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    return str(order.get("status") or "").lower() in FILLED_STATUSES


def order_dead_without_fill(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    status = str(order.get("status") or "").lower()
    filled = _f(order.get("filled_qty")) or 0.0
    return status in DEAD_NO_FILL and filled <= 0


async def fetch_order(broker: Any, order_id: str) -> dict[str, Any] | None:
    oid = (order_id or "").strip()
    if not oid or broker is None:
        return None
    getter = getattr(broker, "get_order", None)
    if getter is not None:
        raw = await getter(oid)
        return raw if isinstance(raw, dict) else None
    req = getattr(broker, "_request", None)
    if req is not None:
        raw = await req("GET", f"/v2/orders/{oid}")
        return raw if isinstance(raw, dict) else None
    return None


async def fetch_cfees(broker: Any, *, after: str | None = None) -> list[dict[str, Any]]:
    getter = getattr(broker, "list_account_activities", None)
    if getter is None:
        return []
    try:
        rows = await getter(activity_types="CFEE", after=after, page_size=100)
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


async def resolve_filled_qty(
    broker: Any,
    *,
    symbol: str,
    order_id: str | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wait-free resolve: only accept filled / partially_filled orders."""
    order = None
    if order_id:
        try:
            order = await fetch_order(broker, order_id)
        except Exception as exc:
            return {"ok": False, "reason": f"order_lookup_failed:{exc}", "qty": None}
    if order is None and isinstance(payload, dict):
        order = payload
    if order_dead_without_fill(order):
        return {"ok": False, "reason": "canceled_or_expired_no_fill", "qty": None, "order": order}
    if not order_is_filled(order):
        return {
            "ok": False,
            "reason": f"order_not_filled:{((order or {}).get('status') or 'missing')}",
            "qty": None,
            "order": order,
        }
    filled = _f(order.get("filled_qty")) or 0.0
    if filled <= 0:
        return {"ok": False, "reason": "filled_qty_zero", "qty": None, "order": order}
    avg = _f(order.get("filled_avg_price") or order.get("filled_avg_px"))
    cfee = 0.0
    try:
        created = str(order.get("created_at") or order.get("submitted_at") or "")[:10]
        cfees = await fetch_cfees(broker, after=created or None)
        cfee = cfee_qty_for_symbol(cfees, symbol)
    except Exception:
        cfee = 0.0
    net = buy_net_qty(filled, rate=BUY_NET_QTY_RATE)
    cfee_net = net_qty_from_fill_and_cfee(filled_qty=filled, cfee_qty=cfee)
    return {
        "ok": True,
        "qty": net,
        "filled_qty": filled,
        "cfee_qty": cfee,
        "cfee_net_qty": cfee_net,
        "net_qty_rate": BUY_NET_QTY_RATE,
        "avg_price": avg,
        "order": order,
    }
