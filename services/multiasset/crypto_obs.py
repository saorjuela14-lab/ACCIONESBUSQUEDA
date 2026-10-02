"""Per-position stop observability for Strategy A PAPER (last-cycle + desk status)."""

from __future__ import annotations

from typing import Any


def _norm(symbol: str) -> str:
    s = (symbol or "").upper().replace(" ", "")
    if "/" not in s and s.endswith("USD") and len(s) > 3:
        base = s[:-3]
        if base.isalpha():
            return f"{base}/USD"
    return s


def position_stop_fields(
    *,
    stop_evaluated_at: str | None,
    candle_close: float | None,
    stop_px: float | None,
    broker_stop: str | None = "none",
    max_close: float | None = None,
    state_source: str | None = None,
    clock_evaluated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "stop_evaluated_at": stop_evaluated_at,
        "clock_evaluated_at": clock_evaluated_at,
        "candle_close": candle_close,
        "stop_px": stop_px,
        "max_close": max_close,
        "broker_stop": broker_stop or "none",
        "state_source": state_source or "db",
    }


def overlay_open_positions(
    positions: list[dict[str, Any]] | None,
    state_positions: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach stop obs to broker position dicts. Missing broker_stop → none (never a false GTC)."""
    state = state_positions or {}
    out: list[dict[str, Any]] = []
    for p in positions or []:
        item = dict(p)
        key = _norm(str(item.get("symbol") or ""))
        st = state.get(key) or state.get(str(item.get("symbol") or "").upper()) or {}
        item.update(
            position_stop_fields(
                stop_evaluated_at=st.get("stop_evaluated_at"),
                candle_close=st.get("candle_close"),
                stop_px=st.get("stop_px"),
                broker_stop=st.get("broker_stop") or "none",
                max_close=st.get("highest_close") or st.get("max_close"),
                state_source=st.get("state_source"),
                clock_evaluated_at=st.get("clock_evaluated_at"),
            )
        )
        out.append(item)
    return out


def open_positions_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    pos = (state or {}).get("positions") or {}
    rows: list[dict[str, Any]] = []
    for sym, st in pos.items():
        if not isinstance(st, dict):
            continue
        row = {"symbol": sym, **position_stop_fields(
            stop_evaluated_at=st.get("stop_evaluated_at"),
            candle_close=st.get("candle_close"),
            stop_px=st.get("stop_px"),
            broker_stop=st.get("broker_stop") or "none",
            max_close=st.get("highest_close") or st.get("max_close"),
            state_source=st.get("state_source"),
            clock_evaluated_at=st.get("clock_evaluated_at"),
        )}
        rows.append(row)
    return rows


_FILLED = frozenset({"filled", "partially_filled"})
_PENDING = frozenset(
    {
        "pending_new",
        "new",
        "accepted",
        "held",
        "accepted_for_bidding",
        "pending_replace",
        "calculated",
    }
)
_REJECTED = frozenset({"rejected", "canceled", "cancelled", "expired", "done_for_day"})


def classify_cycle_side(rows: list[Any] | None, *, side: str) -> dict[str, Any]:
    """Split submitted vs filled vs pending/rejected by order_id.

    A pending_new must never increment ``{side}s`` / ``{side}s_filled``.
    """
    prefix = "buy" if side == "buy" else "sell"
    submitted: list[dict[str, Any]] = []
    filled: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return {
            f"{prefix}s_submitted": 0,
            f"{prefix}s_filled": 0,
            f"{prefix}s_pending": [],
            f"{prefix}s_rejected": [],
            f"{prefix}s": 0,
        }
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        oid = raw.get("order_id") or raw.get("id")
        status = str(raw.get("status") or raw.get("order_status") or "").strip().lower()
        item = {
            "order_id": oid,
            "symbol": raw.get("symbol"),
            "status": status or None,
            "reason": raw.get("reason") or raw.get("error"),
        }
        submitted.append(item)
        if raw.get("error") or status in _REJECTED:
            rejected.append(item)
            continue
        if status in _FILLED:
            filled.append(item)
            continue
        if status in _PENDING:
            pending.append(item)
            continue
        if raw.get("tracked") is True and status not in _PENDING:
            filled.append(item)
            continue
        if raw.get("ok") is True and raw.get("tracked") is True:
            filled.append(item)
            continue
        if raw.get("ok") is False or raw.get("tracked") is False:
            pending.append(item)
            continue
        pending.append(item)
    return {
        f"{prefix}s_submitted": len(submitted),
        f"{prefix}s_filled": len(filled),
        f"{prefix}s_pending": pending,
        f"{prefix}s_rejected": rejected,
        f"{prefix}s": len(filled),
    }


def classify_cycle_fills(desk: dict[str, Any] | None) -> dict[str, Any]:
    src = desk or {}
    out = {}
    out.update(classify_cycle_side(src.get("buys") or [], side="buy"))
    out.update(classify_cycle_side(src.get("sells") or [], side="sell"))
    rejects = list(src.get("spread_rejects") or [])
    out["spread_rejects"] = rejects
    out["spread_reject_count"] = len(rejects)
    return out


def attach_last_cycle_obs(cycle: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any]:
    """Ensure last-cycle crypto desks expose per-position stop fields and no false GTC."""
    last = dict(cycle or {})
    desks = last.get("desks")
    if not isinstance(desks, dict):
        desks = {}
        last["desks"] = desks
    crypto = desks.get("crypto")
    if not isinstance(crypto, dict):
        crypto = {}
        desks["crypto"] = crypto
    obs = open_positions_from_state(state)
    if obs:
        crypto["open_positions"] = obs
    elif "open_positions" not in crypto:
        crypto["open_positions"] = crypto.get("open_positions") or []
    crypto["broker_stops_gtc"] = False
    crypto["broker_stop"] = "none"
    crypto["legacy_engine"] = "off"
    if isinstance(crypto.get("buys"), list) or isinstance(crypto.get("sells"), list):
        counts = classify_cycle_fills(crypto)
        for key, val in counts.items():
            crypto[key] = val
        last["spread_reject_count"] = crypto.get("spread_reject_count", 0)
    else:
        crypto.setdefault("buys_submitted", crypto.get("buys") if isinstance(crypto.get("buys"), int) else 0)
        crypto.setdefault("buys_filled", 0)
        crypto.setdefault("buys_pending", [])
        crypto.setdefault("buys_rejected", [])
        crypto.setdefault("sells_submitted", crypto.get("sells") if isinstance(crypto.get("sells"), int) else 0)
        crypto.setdefault("sells_filled", 0)
        crypto.setdefault("sells_pending", [])
        crypto.setdefault("sells_rejected", [])
        crypto.setdefault("spread_rejects", [])
        crypto.setdefault("spread_reject_count", 0)
        last.setdefault("spread_reject_count", crypto.get("spread_reject_count", 0))
    last["broker_stops_gtc"] = False
    last["legacy_engine"] = "off"
    if state:
        last["last_evaluated_candle"] = state.get("last_evaluated_candle")
        last["eval_history"] = list(state.get("eval_history") or [])[-6:]
        last["missed_candles"] = state.get("missed_candles")
        last["replica_id"] = state.get("replica_id")
        last["candles_behind"] = state.get("candles_behind")
        last["cursors"] = state.get("cursors")
        crypto["last_evaluated_candle"] = last.get("last_evaluated_candle")
        crypto["eval_history"] = last.get("eval_history")
        crypto["missed_candles"] = last.get("missed_candles")
        crypto["replica_id"] = last.get("replica_id")
        crypto["candles_behind"] = last.get("candles_behind")
        crypto["cursors"] = last.get("cursors")
        lease = state.get("lease") if isinstance(state.get("lease"), dict) else {}
        if lease:
            last["lease_owner"] = lease.get("lease_owner") or lease.get("owner")
            last["lease_expires_at"] = lease.get("lease_expires_at") or lease.get("expires_at")
            last["lease_misses_consecutive"] = lease.get("lease_misses_consecutive") or lease.get("misses_consecutive")
            crypto["lease_owner"] = last.get("lease_owner")
            crypto["lease_expires_at"] = last.get("lease_expires_at")
            crypto["lease_misses_consecutive"] = last.get("lease_misses_consecutive")
    return last
