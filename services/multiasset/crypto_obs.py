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
) -> dict[str, Any]:
    return {
        "stop_evaluated_at": stop_evaluated_at,
        "candle_close": candle_close,
        "stop_px": stop_px,
        "broker_stop": broker_stop or "none",
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
        )}
        rows.append(row)
    return rows


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
    last["broker_stops_gtc"] = False
    return last
