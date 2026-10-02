"""PAPER-only inherited August crypto: cancel-by-id + explicit flatten.

Never auto-flattens. Never DELETE /v2/orders (other desks share the account).
Real close is DELETE /v2/positions/{symbol}. Dust is reported, not retried.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.multiasset.crypto_fills import is_dust
from services.multiasset.crypto_owned import (
    AUGUST_INHERITED,
    FLAG_LEGACY_FLAT,
    is_strategy_a_symbol,
    is_strategy_a_trade,
    set_strategy_a_armed,
    strategy_a_is_armed,
)
from services.multiasset.paper_broker import (
    MultiAssetNotPaperError,
    assert_beta_account_is_paper,
    beta_base_url_is_paper,
)
from utils.logging import get_logger
from utils.metrics import metrics

logger = get_logger(__name__)

STALE_GTC = ("WIF/USD", "LDO/USD", "RENDER/USD")
STALE_STATUSES = frozenset({"new", "accepted"})
WORKING = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "held",
        "calculated",
        "pending_cancel",
        "pending_replace",
    }
)


def _norm_sym(symbol: str) -> str:
    s = (symbol or "").upper().replace(" ", "")
    if "/" not in s and s.endswith("USD") and len(s) > 3:
        base = s[:-3]
        if base.isalpha():
            return f"{base}/USD"
    return s


_ETF_USD = frozenset({"GLD", "IAU", "GLDM", "UUP", "FXE", "FXB", "FXY"})


def is_crypto_symbol(symbol: str) -> bool:
    s = (symbol or "").upper().replace(" ", "")
    if s in _ETF_USD:
        return False
    if "/" in s:
        return True
    if s.endswith("USD") and len(s) > 3 and s[:-3].isalpha():
        return s[:-3] not in _ETF_USD
    return False


def is_crypto_order(raw: dict[str, Any]) -> bool:
    cls = str(raw.get("asset_class") or raw.get("asset_class_name") or "").lower()
    if cls == "crypto":
        return True
    return is_crypto_symbol(str(raw.get("symbol") or ""))


def paper_guard(broker: Any) -> dict[str, Any] | None:
    base = getattr(broker, "base_url", "") or ""
    if not beta_base_url_is_paper(base):
        logger.warning("crypto.legacy.refused_not_paper_url", base_url=str(base)[:80])
        return {"skipped": "not_paper_url", "cancelled": [], "closed": []}
    if getattr(broker, "paper", None) is not True:
        logger.warning("crypto.legacy.refused_paper_false")
        return {"skipped": "broker_paper_false", "cancelled": [], "closed": []}
    return None


def require_paper_broker(broker: Any) -> None:
    """Hard refuse. Checks the client URL + paper flag — not an env var."""
    skip = paper_guard(broker)
    if skip:
        raise MultiAssetNotPaperError(
            "Crypto legacy / multiasset ops solo en paper-api.alpaca.markets "
            f"(base={getattr(broker, 'base_url', '')!r} paper={getattr(broker, 'paper', None)!r})."
        )


def _as_dict(od: Any) -> dict[str, Any]:
    if isinstance(od, dict):
        return od
    raw = getattr(od, "raw", None)
    if isinstance(raw, dict):
        return raw
    if hasattr(od, "model_dump"):
        dumped = od.model_dump()
        if isinstance(dumped, dict):
            return dumped
    return {}


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fill_px(raw: dict[str, Any]) -> float | None:
    for key in ("filled_avg_price", "avg_entry_price", "price", "avg_price"):
        px = _f(raw.get(key))
        if px and px > 0:
            return px
    return None


async def cancel_stale_gtc_buys(broker: Any) -> dict[str, Any]:
    """Cancel WIF/LDO/RENDER market buys stuck in new/accepted. Never stops/exits."""
    skip = paper_guard(broker)
    if skip:
        return skip
    try:
        await assert_beta_account_is_paper(broker)
    except MultiAssetNotPaperError as exc:
        return {"skipped": "not_paper", "cancelled": [], "error": str(exc)}
    if not getattr(broker, "is_configured", lambda: False)():
        return {"skipped": "broker_unconfigured", "cancelled": []}

    try:
        orders = await broker.list_orders(status="open", limit=500)
    except Exception as exc:
        logger.warning("crypto.legacy.list_orders_failed", error=str(exc))
        return {"skipped": "list_failed", "cancelled": [], "error": str(exc)}

    want = {_norm_sym(s) for s in STALE_GTC}
    cancelled: list[str] = []
    for od in orders or []:
        raw = _as_dict(od)
        sym = _norm_sym(str(raw.get("symbol") or getattr(od, "symbol", "") or ""))
        side = str(raw.get("side") or getattr(od, "side", "") or "").lower()
        otype = str(raw.get("type") or raw.get("order_type") or getattr(od, "type", "") or "").lower()
        status = str(raw.get("status") or getattr(od, "status", "") or "").lower()
        if sym not in want or side != "buy" or otype != "market" or status not in STALE_STATUSES:
            continue
        oid = str(raw.get("id") or getattr(od, "id", "") or "")
        if not oid:
            continue
        try:
            await broker.cancel_order(oid)
            cancelled.append(oid)
        except Exception as exc:
            logger.warning("crypto.legacy.cancel_failed", id=oid, error=str(exc))
    if cancelled:
        metrics.inc("crypto_legacy_gtc_cancelled", len(cancelled))
        logger.info("crypto.legacy.gtc_cancelled", ids=cancelled)
    return {"skipped": None, "cancelled": cancelled, "count": len(cancelled)}


async def cancel_open_crypto_orders(broker: Any) -> dict[str, Any]:
    """Cancel working crypto orders one-by-one by id. Never DELETE /v2/orders."""
    require_paper_broker(broker)
    await assert_beta_account_is_paper(broker)
    if not getattr(broker, "is_configured", lambda: False)():
        return {"skipped": "broker_unconfigured", "cancelled": [], "paper": True}
    if hasattr(broker, "cancel_all_orders"):
        # Belt: never call the bulk cancel even if a caller mixes this up.
        pass
    try:
        orders = await broker.list_orders(status="open", limit=500)
    except Exception as exc:
        return {"skipped": "list_failed", "cancelled": [], "error": str(exc), "paper": True}

    cancelled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for od in orders or []:
        raw = _as_dict(od)
        if not is_crypto_order(raw):
            skipped.append(
                {
                    "id": raw.get("id"),
                    "symbol": raw.get("symbol"),
                    "reason": "not_crypto",
                }
            )
            continue
        status = str(raw.get("status") or "").lower()
        if status and status not in WORKING:
            continue
        oid = str(raw.get("id") or "")
        if not oid:
            continue
        try:
            await broker.cancel_order(oid)
            cancelled.append(
                {
                    "id": oid,
                    "symbol": _norm_sym(str(raw.get("symbol") or "")),
                    "status": status,
                }
            )
        except Exception as exc:
            cancelled.append({"id": oid, "error": str(exc)})
    logger.info("crypto.legacy.crypto_orders_cancelled", n=len(cancelled))
    return {
        "paper": True,
        "cancelled": cancelled,
        "skipped_not_crypto": skipped,
        "count": len([c for c in cancelled if not c.get("error")]),
    }


def _position_row(raw: dict[str, Any]) -> dict[str, Any]:
    qty = _f(raw.get("qty") or raw.get("qty_available")) or 0.0
    px = _f(raw.get("current_price") or raw.get("lastday_price") or raw.get("avg_entry_price")) or 0.0
    mv = _f(raw.get("market_value"))
    if mv is None and qty and px:
        mv = qty * px
    return {
        "symbol": _norm_sym(str(raw.get("symbol") or "")),
        "qty": qty,
        "avg_entry_price": _f(raw.get("avg_entry_price")),
        "market_value": mv,
        "current_price": px or None,
        "asset_class": raw.get("asset_class") or ("crypto" if is_crypto_symbol(str(raw.get("symbol") or "")) else None),
        "unrealized_pl": _f(raw.get("unrealized_pl")),
    }


async def list_paper_positions(broker: Any) -> dict[str, Any]:
    require_paper_broker(broker)
    await assert_beta_account_is_paper(broker)
    if not getattr(broker, "is_configured", lambda: False)():
        return {"paper": True, "items": [], "count": 0, "skipped": "broker_unconfigured"}
    rows = await broker.get_positions()
    items = [_position_row(_as_dict(p)) for p in rows or []]
    return {"paper": True, "items": items, "count": len(items)}


ORDER_FIELDS = (
    "id",
    "client_order_id",
    "symbol",
    "side",
    "type",
    "time_in_force",
    "status",
    "qty",
    "notional",
    "filled_qty",
    "filled_avg_price",
    "created_at",
    "filled_at",
    "asset_class",
    "submitted_at",
    "canceled_at",
    "expired_at",
)


async def list_paper_orders(
    broker: Any,
    *,
    status: str = "open",
    page_token: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    require_paper_broker(broker)
    await assert_beta_account_is_paper(broker)
    want = (status or "open").strip().lower()
    if want not in {"open", "closed", "all"}:
        want = "open"
    if not getattr(broker, "is_configured", lambda: False)():
        return {
            "paper": True,
            "status": want,
            "items": [],
            "count": 0,
            "next_page_token": None,
            "skipped": "broker_unconfigured",
        }
    lister = getattr(broker, "list_orders", None)
    raw_rows: list[Any] = []
    next_token = None
    if lister is not None:
        try:
            raw_rows = await lister(status=want, limit=min(int(page_size or 100), 500))
        except TypeError:
            raw_rows = await lister(status=want, limit=min(int(page_size or 100), 500))
    items: list[dict[str, Any]] = []
    for od in raw_rows or []:
        raw = _as_dict(od)
        row = {k: raw.get(k) for k in ORDER_FIELDS if raw.get(k) is not None}
        if raw.get("symbol"):
            row["symbol"] = _norm_sym(str(raw.get("symbol")))
        if "time_in_force" not in row and raw.get("tif"):
            row["time_in_force"] = raw.get("tif")
        items.append(row)
    size = min(int(page_size or 100), 500)
    if page_token:
        start = 0
        for i, row in enumerate(items):
            if str(row.get("id") or "") == str(page_token):
                start = i + 1
                break
        items = items[start:]
    next_token = items[size].get("id") if len(items) > size else None
    items = items[:size]
    return {
        "paper": True,
        "status": want,
        "items": items,
        "count": len(items),
        "next_page_token": next_token,
        "page_token": page_token,
    }


def a_has_open_lot(trades: list[Any], symbol: str) -> bool:
    want = _norm_sym(symbol)
    for t in trades or []:
        if _norm_sym(getattr(t, "symbol", "")) == want and is_strategy_a_trade(t):
            return True
    return False


async def legacy_flatten(
    broker: Any,
    *,
    symbols: list[str],
    dry_run: bool = True,
    actor: str,
    tracker: Any = None,
    flags: Any = None,
) -> dict[str, Any]:
    """Explicit flatten. dry_run=True by default. Never auto-called."""
    require_paper_broker(broker)
    await assert_beta_account_is_paper(broker)
    asked = [_norm_sym(s) for s in (symbols or []) if str(s).strip()]
    if not asked:
        raise ValueError("symbols: lista explícita requerida")

    open_a: list[Any] = []
    if tracker is not None:
        try:
            open_a = await tracker.list_open(desk="crypto")
        except Exception:
            open_a = []
    armed = await strategy_a_is_armed(flags, inherited=True) if flags is not None else False
    blocked: list[str] = []
    if armed:
        for sym in asked:
            if is_strategy_a_symbol(sym) and a_has_open_lot(open_a, sym):
                blocked.append(sym)
    # After A is armed: skip BTC/ETH with an A lot; still flatten the other inherited names.

    cancels: dict[str, Any] = {"cancelled": [], "dry_run": dry_run}
    try:
        if dry_run:
            orders = await list_paper_orders(broker, status="open")
            cancels = {
                "dry_run": True,
                "would_cancel": [
                    o
                    for o in (orders.get("items") or [])
                    if is_crypto_order(o) or is_crypto_symbol(str(o.get("symbol") or ""))
                ],
            }
        else:
            cancels = await cancel_open_crypto_orders(broker)
    except MultiAssetNotPaperError:
        raise
    except Exception as exc:
        cancels = {"error": str(exc), "cancelled": []}

    try:
        positions = await broker.get_positions()
    except Exception as exc:
        return {"ok": False, "paper": True, "dry_run": dry_run, "actor": actor, "error": str(exc)}

    by_sym: dict[str, dict[str, Any]] = {}
    for pos in positions or []:
        raw = _as_dict(pos)
        row = _position_row(raw)
        if row["symbol"]:
            by_sym[row["symbol"]] = {**row, "_raw": raw}

    preview: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    dust: list[dict[str, Any]] = []
    missing: list[str] = []
    for sym in asked:
        if sym in blocked:
            preview.append({"symbol": sym, "rejected": "strategy_a_open_lot"})
            continue
        row = by_sym.get(sym)
        if not row:
            missing.append(sym)
            preview.append({"symbol": sym, "qty": 0, "market_value": 0, "missing": True})
            continue
        preview.append(
            {
                "symbol": sym,
                "qty": row["qty"],
                "avg_entry_price": row["avg_entry_price"],
                "market_value": row["market_value"],
            }
        )
        if dry_run:
            continue
        try:
            result = await broker.close_position(sym)
        except Exception as exc:
            closed.append({"symbol": sym, "error": str(exc)})
            continue
        fill = _as_dict(result)
        px = _fill_px(fill) or _fill_px(row) or row.get("current_price")
        qty = float(row["qty"] or 0)
        entry = float(row.get("avg_entry_price") or 0)
        pnl = (float(px) - entry) * qty if px and entry and qty else None
        if tracker is not None and px:
            try:
                await tracker.close_trade(
                    desk="crypto",
                    symbol=sym,
                    exit_price=float(px),
                    exit_reason="legacy_august_flatten_paper",
                )
            except Exception as exc:
                logger.warning("crypto.legacy.journal_failed", symbol=sym, error=str(exc))
        closed.append(
            {
                "symbol": sym,
                "qty": qty,
                "entry": entry,
                "exit": px,
                "pnl_usd": round(pnl, 4) if pnl is not None else None,
                "order_id": fill.get("id"),
            }
        )
        leftover = _f(fill.get("qty")) if fill.get("status") not in {"filled", None, ""} else 0.0
        # Re-read residue: if still listed under min lot, report dust, do not loop.
        try:
            leftover_pos = None
            fresh = await broker.get_positions()
            for p in fresh or []:
                pr = _position_row(_as_dict(p))
                if pr["symbol"] == sym:
                    leftover_pos = pr
                    break
            if leftover_pos and is_dust(
                float(leftover_pos["qty"] or 0),
                float(leftover_pos.get("current_price") or leftover_pos.get("avg_entry_price") or 0),
                symbol=sym,
            ):
                dust.append(
                    {
                        "symbol": sym,
                        "qty": leftover_pos["qty"],
                        "market_value": leftover_pos.get("market_value"),
                    }
                )
            elif leftover and leftover_pos is None:
                pass
        except Exception:
            leftover = leftover

    all_blocked = bool(blocked) and not closed and not dry_run and not missing
    out = {
        "ok": not (blocked and not closed and not dry_run),
        "paper": True,
        "dry_run": bool(dry_run),
        "actor": actor,
        "symbols": asked,
        "preview": preview,
        "closed": closed,
        "dust": dust,
        "missing": missing,
        "rejected": blocked,
        "cancels": cancels,
        "at": datetime.now(timezone.utc).isoformat(),
        "legacy_engine": "off",
        "error": "strategy_a_open_lot" if all_blocked else None,
    }
    if flags is not None and not dry_run and closed:
        await flags.set_json(
            FLAG_LEGACY_FLAT,
            {"done": True, "actor": actor, **{k: v for k, v in out.items() if k != "preview"}},
        )
        await set_strategy_a_armed(flags, armed=False, actor=actor)
    return out


async def close_inherited_crypto_positions(
    broker: Any,
    *,
    tracker: Any = None,
    desk: str = "crypto",
) -> dict[str, Any]:
    """Explicit market-close of paper crypto positions. Not called by Autopilot."""
    skip = paper_guard(broker)
    if skip:
        return skip
    try:
        await assert_beta_account_is_paper(broker)
    except MultiAssetNotPaperError as exc:
        return {"skipped": "not_paper", "closed": [], "error": str(exc)}
    if not getattr(broker, "is_configured", lambda: False)():
        return {"skipped": "broker_unconfigured", "closed": []}

    try:
        positions = await broker.get_positions()
    except Exception as exc:
        logger.warning("crypto.legacy.positions_failed", error=str(exc))
        return {"skipped": "positions_failed", "closed": [], "error": str(exc)}

    closed: list[dict[str, Any]] = []
    realized = 0.0
    for pos in positions or []:
        raw = _as_dict(pos)
        sym = _norm_sym(str(raw.get("symbol") or getattr(pos, "symbol", "") or ""))
        if not is_crypto_symbol(sym):
            continue
        try:
            result = await broker.close_position(sym)
        except Exception as exc:
            closed.append({"symbol": sym, "error": str(exc)})
            continue
        fill = _as_dict(result)
        px = _fill_px(fill) or _fill_px(raw)
        qty = _f(raw.get("qty") or getattr(pos, "qty", 0)) or 0.0
        entry = _f(raw.get("avg_entry_price") or getattr(pos, "avg_entry_price", 0)) or 0.0
        pnl = None
        if px and entry and qty:
            pnl = (px - entry) * qty
            realized += pnl
        if tracker is not None and px:
            try:
                await tracker.close_trade(
                    desk=desk,
                    symbol=sym,
                    exit_price=float(px),
                    exit_reason="legacy_august_flatten_paper",
                )
            except Exception as exc:
                logger.warning("crypto.legacy.journal_failed", symbol=sym, error=str(exc))
        closed.append(
            {
                "symbol": sym,
                "qty": qty,
                "entry": entry,
                "exit": px,
                "pnl_usd": round(pnl, 4) if pnl is not None else None,
                "order_id": fill.get("id"),
            }
        )
    return {
        "skipped": None,
        "closed": closed,
        "count": len([c for c in closed if not c.get("error")]),
        "realized_pnl_usd": round(realized, 4),
        "paper": True,
    }


async def flatten_before_strategy_a(
    broker: Any,
    *,
    tracker: Any = None,
    flags: Any = None,
) -> dict[str, Any]:
    """Retired auto-flatten. Quarantine only — mesa runs POST /ops/crypto/legacy-flatten."""
    del broker, tracker
    if flags is not None:
        prev = await flags.get_json(FLAG_LEGACY_FLAT)
        if prev.get("done"):
            return {"skipped": "already_done", "previous": prev, "auto": False}
    logger.info("crypto.legacy.auto_flatten_disabled")
    return {
        "skipped": "manual_only",
        "auto": False,
        "hint": "POST /ops/crypto/legacy-flatten dry_run=true + lista explícita",
        "inherited": list(AUGUST_INHERITED),
    }
