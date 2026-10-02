"""Paper-only Multi-Asset ops: activity reads + stale market-buy cleanup.

Never runs against LIVE. Never cancels stops, exit limits, or positions.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from services.multiasset.paper_broker import (
    PAPER_HOST,
    MultiAssetNotPaperError,
    assert_beta_account_is_paper,
    beta_base_url_is_paper,
)
from utils.logging import get_logger
from utils.metrics import metrics

logger = get_logger(__name__)

STALE_MARKET_BUY_HOURS = 24.0
ACTIVITY_MAX_PAGES = 20
ACTIVITY_PAGE_SIZE_CAP = 100
STALE_BUY_STATUSES = frozenset({"new", "accepted"})
ACTIVITY_SAFE_FIELDS = (
    "id",
    "activity_type",
    "activityType",
    "transaction_time",
    "transaction_time_utc",
    "date",
    "type",
    "symbol",
    "qty",
    "price",
    "side",
    "net_amount",
    "description",
    "order_id",
    "order_status",
    "leaves_qty",
    "cum_qty",
    "per_share_amount",
    "status",
)


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_utc_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_date_only_bound(value: str | None) -> bool:
    return bool(value) and bool(_DATE_ONLY.fullmatch(str(value).strip()))


def activity_types_include_cfee(types: str | Iterable[str] | None) -> bool:
    if types is None:
        return False
    if isinstance(types, str):
        parts = [p.strip().upper() for p in types.split(",") if p.strip()]
    else:
        parts = [str(p).strip().upper() for p in types if str(p).strip()]
    return "CFEE" in parts


def normalize_activity_bound(value: str | None, *, kind: str) -> str | None:
    """Alpaca ``after``/``until`` are exclusive on created_at. Date-only is UTC midnight.

    ``after`` YYYY-MM-DD → that day 00:00:00Z (created_at after midnight includes the day).
    ``until`` YYYY-MM-DD → next day 00:00:00Z so the requested day stays included.
    Full timestamps are converted to UTC ``Z`` without shifting the instant.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if is_date_only_bound(raw):
        day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if kind == "until":
            day = day + timedelta(days=1)
        return _to_utc_z(day)
    dt = _parse_dt(raw)
    if dt is None:
        raise ValueError(f"{kind} debe ser YYYY-MM-DD o un timestamp ISO UTC")
    return _to_utc_z(dt)


def resolve_activity_window(
    after: str | None,
    until: str | None,
    types: str | Iterable[str] | None,
) -> tuple[str | None, str | None, str | None]:
    """Return (after_effective, until_normalized, until_effective).

    CFEE extends ``until`` one extra UTC day — Alpaca posts commissions the day after the fill.
    """
    after_n = normalize_activity_bound(after, kind="after")
    until_n = normalize_activity_bound(until, kind="until")
    until_eff = until_n
    if until_eff and activity_types_include_cfee(types):
        dt = _parse_dt(until_eff)
        if dt is None:
            raise ValueError("until inválido")
        until_eff = _to_utc_z(dt + timedelta(days=1))
    return after_n, until_n, until_eff


def is_stale_market_buy(
    order: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float = STALE_MARKET_BUY_HOURS,
) -> bool:
    """True for a working MARKET buy older than max_age. Never stops/limits/sells."""
    if not isinstance(order, dict):
        return False
    side = str(order.get("side") or "").lower()
    otype = str(order.get("type") or order.get("order_type") or "").lower()
    status = str(order.get("status") or "").lower()
    if side != "buy":
        return False
    if otype != "market":
        return False
    if status not in STALE_BUY_STATUSES:
        return False
    created = _parse_dt(order.get("created_at") or order.get("submitted_at"))
    if created is None:
        return False
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    return (now_dt - created) > timedelta(hours=float(max_age_hours))


def normalize_activity_symbol(symbol: str | None) -> str | None:
    """BTCUSD and BTC/USD are the same paper crypto pair."""
    if symbol is None:
        return None
    raw = str(symbol).strip()
    if not raw:
        return None
    from services.multiasset.desks import normalize_symbol

    return normalize_symbol(raw)


def sanitize_activity(row: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that is not a known activity field (no keys/secrets)."""
    out: dict[str, Any] = {}
    for key in ACTIVITY_SAFE_FIELDS:
        if key in row and row[key] is not None:
            out[key if key != "activityType" else "activity_type"] = row[key]
    at = row.get("activity_type") or row.get("activityType") or row.get("type")
    if at and "activity_type" not in out:
        out["activity_type"] = at
    if "symbol" in out:
        out["symbol"] = normalize_activity_symbol(out.get("symbol")) or out["symbol"]
    return out


def paper_guard_or_skip(broker: Any) -> dict[str, Any] | None:
    """None if paper-api + paper flag. Else a skip payload (do nothing)."""
    base = getattr(broker, "base_url", "") or ""
    if not beta_base_url_is_paper(base):
        logger.warning("multiasset.paper_ops.refused_not_paper_url", base_url=str(base)[:80])
        return {"skipped": "not_paper_url", "cancelled": [], "items": []}
    if getattr(broker, "paper", None) is not True:
        logger.warning("multiasset.paper_ops.refused_paper_false")
        return {"skipped": "broker_paper_false", "cancelled": [], "items": []}
    return None


async def list_paper_activities(
    broker: Any,
    *,
    types: str | list[str] = "FILL,CFEE",
    after: str | None = None,
    until: str | None = None,
    page_size: int = 100,
    page_token: str | None = None,
    direction: str = "desc",
    max_pages: int = ACTIVITY_MAX_PAGES,
) -> dict[str, Any]:
    """Read-only Alpaca paper activities. Walks page_token until exhausted."""
    skip = paper_guard_or_skip(broker)
    if skip:
        raise MultiAssetNotPaperError(
            "Activities Multi-Asset solo en paper-api.alpaca.markets "
            f"(base={getattr(broker, 'base_url', '')!r})."
        )
    await assert_beta_account_is_paper(broker)
    direction_n = (direction or "desc").strip().lower()
    if direction_n not in {"asc", "desc"}:
        direction_n = "desc"
    size = max(1, min(int(page_size or 100), ACTIVITY_PAGE_SIZE_CAP))
    cap = max(1, int(max_pages or ACTIVITY_MAX_PAGES))
    after_eff, until_norm, until_eff = resolve_activity_window(after, until, types)
    if not getattr(broker, "is_configured", lambda: False)():
        return {
            "paper": True,
            "host": PAPER_HOST,
            "items": [],
            "count": 0,
            "truncated": False,
            "next_page_token": None,
            "page_token": page_token,
            "after": after,
            "until": until,
            "until_effective": until_eff,
            "direction": direction_n,
            "skipped": "broker_unconfigured",
        }
    items: list[dict[str, Any]] = []
    token: str | None = (str(page_token).strip() or None) if page_token else None
    truncated = False
    next_token: str | None = None
    for page_i in range(cap):
        page = await broker.list_account_activities(
            activity_types=types,
            after=after_eff,
            until=until_eff,
            page_size=size,
            page_token=token,
            direction=direction_n,
        )
        rows = [r for r in (page or []) if isinstance(r, dict)]
        items.extend(sanitize_activity(r) for r in rows)
        if len(rows) < size:
            next_token = None
            truncated = False
            break
        next_token = str(rows[-1].get("id") or "").strip() or None
        if not next_token:
            truncated = False
            break
        if page_i + 1 >= cap:
            truncated = True
            break
        token = next_token
    else:
        truncated = bool(next_token)
    return {
        "paper": True,
        "host": PAPER_HOST,
        "types": types if isinstance(types, str) else ",".join(types),
        "after": after,
        "until": until,
        "until_effective": until_eff,
        "direction": direction_n,
        "page_token": page_token,
        "page_size": size,
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "next_page_token": next_token if truncated else None,
    }


async def cancel_stale_paper_market_buys(
    broker: Any,
    *,
    now: datetime | None = None,
    max_age_hours: float = STALE_MARKET_BUY_HOURS,
) -> dict[str, Any]:
    """Cancel paper MARKET buys stuck in new/accepted > 24h. No-op on LIVE URL."""
    skip = paper_guard_or_skip(broker)
    if skip:
        return skip
    try:
        await assert_beta_account_is_paper(broker)
    except MultiAssetNotPaperError as exc:
        logger.warning("multiasset.stale_buys.refused_not_paper", error=str(exc))
        return {"skipped": "not_paper", "cancelled": [], "error": str(exc)}
    if not getattr(broker, "is_configured", lambda: False)():
        return {"skipped": "broker_unconfigured", "cancelled": []}

    try:
        open_orders = await broker.list_orders(status="open", limit=500)
    except Exception as exc:
        logger.warning("multiasset.stale_buys.list_failed", error=str(exc))
        return {"skipped": "list_failed", "cancelled": [], "error": str(exc)}

    now_dt = now or datetime.now(timezone.utc)
    to_cancel: list[dict[str, Any]] = []
    for od in open_orders or []:
        raw = od if isinstance(od, dict) else getattr(od, "raw", None) or {}
        if not isinstance(raw, dict) and hasattr(od, "model_dump"):
            raw = od.model_dump()
        if not isinstance(raw, dict):
            continue
        if is_stale_market_buy(raw, now=now_dt, max_age_hours=max_age_hours):
            to_cancel.append(raw)

    cancelled: list[str] = []
    errors: list[str] = []
    for raw in to_cancel:
        oid = str(raw.get("id") or "")
        if not oid:
            continue
        try:
            await broker.cancel_order(oid)
            cancelled.append(oid)
        except Exception as exc:
            errors.append(f"{oid}: {exc}")

    if cancelled:
        metrics.inc("multiasset_stale_market_buys_cancelled", len(cancelled))
        logger.info(
            "multiasset.stale_market_buys.cancelled",
            ids=cancelled,
            count=len(cancelled),
            symbols=[str(r.get("symbol") or "") for r in to_cancel if r.get("id") in cancelled],
        )
    return {
        "skipped": None,
        "cancelled": cancelled,
        "count": len(cancelled),
        "errors": errors,
        "max_age_hours": max_age_hours,
    }
