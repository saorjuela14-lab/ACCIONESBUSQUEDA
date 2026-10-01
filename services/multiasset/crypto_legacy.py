"""One-shot PAPER flatten of inherited August crypto before Strategy A.

Hard paper-api + paper=true guard. Never touches LIVE.
"""

from __future__ import annotations

from typing import Any

from services.multiasset.paper_broker import (
    MultiAssetNotPaperError,
    assert_beta_account_is_paper,
    beta_base_url_is_paper,
)
from utils.logging import get_logger
from utils.metrics import metrics

logger = get_logger(__name__)

FLAG_LEGACY_FLAT = "crypto_strategy_a_legacy_flat"
STALE_GTC = ("WIF/USD", "LDO/USD", "RENDER/USD")
STALE_STATUSES = frozenset({"new", "accepted"})


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


def paper_guard(broker: Any) -> dict[str, Any] | None:
    base = getattr(broker, "base_url", "") or ""
    if not beta_base_url_is_paper(base):
        logger.warning("crypto.legacy.refused_not_paper_url", base_url=str(base)[:80])
        return {"skipped": "not_paper_url", "cancelled": [], "closed": []}
    if getattr(broker, "paper", None) is not True:
        logger.warning("crypto.legacy.refused_paper_false")
        return {"skipped": "broker_paper_false", "cancelled": [], "closed": []}
    return None


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


def _fill_px(raw: dict[str, Any]) -> float | None:
    for key in ("filled_avg_price", "avg_entry_price", "price", "avg_price"):
        try:
            px = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            return px
    return None


async def close_inherited_crypto_positions(
    broker: Any,
    *,
    tracker: Any = None,
    desk: str = "crypto",
) -> dict[str, Any]:
    """Market-close crypto positions on PAPER. Correct tracker/journal with broker fills."""
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
        qty = 0.0
        try:
            qty = float(raw.get("qty") or getattr(pos, "qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        entry = 0.0
        try:
            entry = float(raw.get("avg_entry_price") or getattr(pos, "avg_entry_price", 0) or 0)
        except (TypeError, ValueError):
            entry = 0.0
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
    if closed:
        metrics.inc("crypto_legacy_positions_closed", len([c for c in closed if not c.get("error")]))
        logger.info(
            "crypto.legacy.positions_closed",
            n=len(closed),
            realized=round(realized, 4),
            symbols=[c.get("symbol") for c in closed],
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
    """Idempotent: cancel 3 GTC + close inherited crypto, once."""
    skip = paper_guard(broker)
    if skip:
        return skip
    if flags is not None:
        prev = await flags.get_json(FLAG_LEGACY_FLAT)
        if prev.get("done"):
            return {"skipped": "already_done", "previous": prev}
    cancels = await cancel_stale_gtc_buys(broker)
    closes = await close_inherited_crypto_positions(broker, tracker=tracker)
    out = {
        "paper": True,
        "cancelled": cancels.get("cancelled") or [],
        "closed": closes.get("closed") or [],
        "realized_pnl_usd": closes.get("realized_pnl_usd") or 0.0,
        "cancel_skip": cancels.get("skipped"),
        "close_skip": closes.get("skipped"),
    }
    if flags is not None and not cancels.get("skipped") and not closes.get("skipped"):
        from datetime import datetime, timezone

        await flags.set_json(
            FLAG_LEGACY_FLAT,
            {"done": True, "at": datetime.now(timezone.utc).isoformat(), **out},
        )
    return out
