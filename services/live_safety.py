"""LIVE stocks-desk safety gates — entries, deposited brake, voice, journal fills.

Does not change Multi-Asset PAPER strategy or risk limits.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from utils.logging import get_logger
from utils.market_hours import MARKET_CLOSE, US_EASTERN, is_market_open, now_et

logger = get_logger(__name__)

# NYSE/NASDAQ common stock tickers (class suffix allowed: BRK.B).
_US_EQUITY_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")

# Crypto / multi-asset roots — never a LIVE equity fill, even if 1–5 letters.
_MULTIASSET_CRYPTO_ROOTS = frozenset({
    "BTC", "ETH", "SOL", "BONK", "SUSHI", "DOGE", "WIF", "ARB", "LDO",
    "RENDER", "BCH", "XTZ", "BAT", "AVAX", "LINK", "UNI", "PEPE", "SHIB",
    "LTC", "DOT", "ATOM",
})

_KILL_OFF_CONFIRM = ("confirma", "confirmado", "autorizo", "explicit", "explícit")
# Human must name the kill-switch; bare "kill" matches "skill" (false positive).
_KILL_OFF_TOPIC = (
    "kill switch",
    "kill-switch",
    "killswitch",
    "freno de emergencia",
    "desactivar kill",
    "apaga el kill",
    "apagá el kill",
)

FLAG_ENTRY_DAY = "live_entry_day_count"
FLAG_SUBMIT_FAILS = "live_submit_fail_streak"
FLAG_STOP_1R = "live_stop_1r_block"


def deposited_brake_floor(base: float, pct: float = 5.0) -> float:
    """95% of deposited base. $21.76 × 0.95 → $20.67."""
    b = float(base or 0.0)
    p = abs(float(pct or 0.0))
    if b <= 0:
        return 0.0
    return round(b * (1.0 - p / 100.0), 2)


def deposited_brake_triggered(
    equity: float | None,
    base: float | None,
    pct: float = 5.0,
) -> bool:
    """True when equity crosses the accumulated 5% floor vs deposited capital."""
    if equity is None or base is None:
        return False
    b = float(base)
    if b <= 0:
        return False
    return float(equity) <= deposited_brake_floor(b, pct)


def live_buys_allowed(*, paper: bool, live_entries_enabled: bool) -> tuple[bool, str]:
    """PAPER keeps its bracket. LIVE new buys require LIVE_ENTRIES_ENABLED."""
    if paper:
        return True, "paper_entries_ok"
    if live_entries_enabled:
        return True, "live_entries_enabled"
    return False, "live_entries_disabled"


def is_buy_side(side: str | None) -> bool:
    return (side or "").strip().lower() == "buy"


def live_entry_blocked(*, side: str | None, paper: bool, live_entries_enabled: bool) -> tuple[bool, str]:
    """True when this order would open or increase a LIVE position while entries are off."""
    if not is_buy_side(side):
        return False, "exit_or_reduce_ok"
    ok, why = live_buys_allowed(paper=paper, live_entries_enabled=live_entries_enabled)
    if ok:
        return False, why
    return True, why


def sell_qty_exceeds_long(qty: float | None, position_qty: float | None) -> bool:
    """True when a sell is larger than the real long. Missing position → exceed (fail closed)."""
    try:
        want = float(qty or 0)
    except (TypeError, ValueError):
        return True
    if want <= 0:
        return True
    try:
        have = float(position_qty) if position_qty is not None else 0.0
    except (TypeError, ValueError):
        return True
    return want > have + 1e-9


def long_qty_from_positions(positions: list[Any] | None, symbol: str) -> float:
    want = (symbol or "").upper().replace("/", "").replace("-", "")
    for p in positions or []:
        if isinstance(p, dict):
            raw = str(p.get("symbol") or "")
            qty = p.get("qty")
        else:
            raw = str(getattr(p, "symbol", "") or "")
            qty = getattr(p, "qty", 0)
        key = raw.upper().replace("/", "").replace("-", "")
        if key == want or key.endswith(want):
            try:
                q = float(qty or 0)
            except (TypeError, ValueError):
                return 0.0
            return q if q > 0 else 0.0
    return 0.0


def is_multiasset_crypto_symbol(ticker: str) -> bool:
    t = (ticker or "").upper().replace(" ", "")
    if not t:
        return False
    if "/" in t:
        return True
    root = t.replace("/USD", "").replace("-USD", "")
    if root.endswith("USD") and len(root) > 3:
        root = root[:-3]
    return root in _MULTIASSET_CRYPTO_ROOTS


def is_us_equity_live_symbol(ticker: str) -> bool:
    """Whitelist: US listed common stocks only, on the LIVE voice/order path."""
    t = (ticker or "").upper().strip()
    if not t or "/" in t or "-" in t:
        return False
    if is_multiasset_crypto_symbol(t):
        return False
    return bool(_US_EQUITY_RE.fullmatch(t))


_KILL_OFF_EXACT = frozenset({
    "confirma desactivar kill switch",
    "confirma desactivar el kill switch",
    "confirma apagar el kill switch",
    "confirmado desactivar kill switch",
    "autorizo desactivar kill switch",
    "sergio confirma desactivar kill switch",
})


def _norm_voice_text(text: str) -> str:
    t = (text or "").lower()
    for src, dst in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ü", "u")):
        t = t.replace(src, dst)
    t = t.replace("-", " ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def voice_kill_off_confirmed(args: dict[str, Any] | None, user_text: str | None = None) -> bool:
    """Exact spoken phrase only. Never reads args['user_text'] (spoofable)."""
    args = args or {}
    confirm = args.get("confirm")
    if confirm is True:
        flagged = True
    else:
        flagged = str(confirm or "").strip().lower() in {"true", "1", "yes", "y", "si", "sí"}
    if not flagged:
        return False
    # Caller must pass the live utterance. Do not fall back to args["user_text"].
    text = str(user_text or "")
    if not text.strip():
        return False
    norm = _norm_voice_text(text)
    if re.search(r"\bno\b", norm):
        return False
    return norm in _KILL_OFF_EXACT


def eod_may_submit_orders(dt: datetime | None = None) -> bool:
    """Equity orders only while the NYSE regular session is open (before 16:00 ET)."""
    return is_market_open(dt)


def skip_reopen_after_hours_close(
    closed_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Do not reopen a journal/mandate after an after-hours close the same ET day."""
    if closed_at is None:
        return False
    now_d = now or now_et()
    if now_d.tzinfo is None:
        now_d = now_d.replace(tzinfo=US_EASTERN)
    else:
        now_d = now_d.astimezone(US_EASTERN)
    closed = closed_at
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=US_EASTERN)
    else:
        closed = closed.astimezone(US_EASTERN)
    if closed.date() != now_d.date():
        return False
    if closed.time() >= MARKET_CLOSE:
        return True
    if now_d.time() >= MARKET_CLOSE:
        return True
    return False


def entry_price_from_fill(
    filled_avg_price: float | None,
    *,
    filled_qty: float | None = None,
    limit_price: float | None = None,
    stop_loss: float | None = None,
) -> float | None:
    """Real fill only. Never persist the stop (or a limit guess) as entry."""
    del limit_price, stop_loss
    try:
        qty = float(filled_qty or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        px = float(filled_avg_price or 0)
    except (TypeError, ValueError):
        px = 0.0
    if px > 0 and (filled_qty is None or qty > 0):
        return px
    return None


def filled_exit_price_from_order(order: Any) -> float | None:
    """Extract a real sell fill; ignore unfilled stop/limit working prices."""
    if order is None:
        return None
    status = str(getattr(order, "status", None) or "")
    if isinstance(order, dict):
        status = str(order.get("status") or "")
        side = str(order.get("side") or "").lower()
        filled_qty = order.get("filled_qty") or order.get("filled_qty")
        px = order.get("filled_avg_price")
        otype = str(order.get("type") or order.get("order_type") or "").lower()
    else:
        side = str(getattr(order, "side", "") or "").lower()
        filled_qty = getattr(order, "filled_qty", None)
        px = getattr(order, "filled_avg_price", None)
        otype = str(getattr(order, "type", "") or "").lower()
    if side and side != "sell":
        return None
    try:
        qty = float(filled_qty or 0)
    except (TypeError, ValueError):
        qty = 0.0
    filled_status = status.lower() in {"filled", "partially_filled", "closed"}
    if qty <= 0 and not filled_status:
        return None
    try:
        price = float(px or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return None
    del otype
    return price


def order_looks_like_stop(order: Any) -> bool:
    if order is None:
        return False
    if isinstance(order, dict):
        otype = str(order.get("type") or order.get("order_type") or "").lower()
        raw = order
    else:
        otype = str(getattr(order, "type", "") or "").lower()
        raw = getattr(order, "raw", None) or {}
    if otype in {"stop", "stop_limit"} or "stop" in otype:
        return True
    if isinstance(raw, dict):
        nested = raw.get("stop_loss") or raw.get("stop_price")
        if nested:
            return True
        for leg in raw.get("legs") or []:
            if isinstance(leg, dict) and order_looks_like_stop(leg):
                return True
    return False


def _level_num(value: Any) -> float | None:
    if isinstance(value, dict):
        value = (
            value.get("stop_price")
            or value.get("limit_price")
            or value.get("price")
            or value.get("stop")
        )
    try:
        px = float(value or 0)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def protective_levels_from_order(order: Any) -> tuple[float | None, float | None]:
    """Real stop / take-profit from a broker order. Never invents 8%/16% defaults."""
    if order is None:
        return None, None
    raw = order if isinstance(order, dict) else (getattr(order, "raw", None) or {})
    if not isinstance(raw, dict):
        raw = {}
    stop = _level_num(raw.get("stop_loss") or raw.get("stop_price"))
    tp = _level_num(raw.get("take_profit") or raw.get("limit_price") if raw.get("take_profit") else None)
    if tp is None and str(raw.get("type") or raw.get("order_type") or "").lower() in {"limit"}:
        tp = _level_num(raw.get("limit_price"))
    if not isinstance(order, dict):
        stop = stop or _level_num(getattr(order, "stop_price", None))
        tp = tp or _level_num(getattr(order, "limit_price", None))
        tp = tp or _level_num(getattr(order, "take_profit", None))
        stop = stop or _level_num(getattr(order, "stop_loss", None))
    for leg in raw.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        ltype = str(leg.get("type") or leg.get("order_type") or "").lower()
        if ltype in {"stop", "stop_limit"} or leg.get("stop_price"):
            stop = stop or _level_num(leg.get("stop_price") or leg.get("stop_loss"))
        if ltype == "limit" or leg.get("limit_price"):
            side = str(leg.get("side") or "").lower()
            if side != "buy":
                tp = tp or _level_num(leg.get("limit_price") or leg.get("take_profit"))
    return stop, tp


def exit_px_from_order_state(order: Any, mandate: Any = None) -> float | None:
    """Best available exit price from order/position state when no fill is journaled."""
    px = filled_exit_price_from_order(order)
    if px:
        return px
    stop, _ = protective_levels_from_order(order)
    if stop:
        return stop
    if mandate is not None:
        try:
            s = float(getattr(mandate, "stop_loss", 0) or 0)
        except (TypeError, ValueError):
            s = 0.0
        if s > 0:
            return s
    return None


def et_today(dt: datetime | None = None) -> str:
    d = dt or now_et()
    if d.tzinfo is None:
        d = d.replace(tzinfo=US_EASTERN)
    else:
        d = d.astimezone(US_EASTERN)
    return d.strftime("%Y-%m-%d")


def entry_day_allowed(flag: dict[str, Any] | None, *, max_entries: int = 1, today: str | None = None) -> tuple[bool, str, dict[str, Any]]:
    today = today or et_today()
    data = dict(flag or {})
    if data.get("et_date") != today:
        data = {"et_date": today, "count": 0, "symbols": []}
    count = int(data.get("count") or 0)
    cap = max(1, int(max_entries or 1))
    if count >= cap:
        return False, "max_1_entry_per_day", data
    return True, "ok", data


def remaining_entry_slots(
    flag: dict[str, Any] | None, *, max_entries: int = 1, today: str | None = None
) -> int:
    """How many new LIVE entries may still be submitted today. Never enlarge the cap."""
    ok, _, data = entry_day_allowed(flag, max_entries=max_entries, today=today)
    if not ok:
        return 0
    cap = max(1, int(max_entries or 1))
    return max(0, cap - int(data.get("count") or 0))


def record_entry_day_fill(flag: dict[str, Any], symbol: str) -> dict[str, Any]:
    data = dict(flag or {})
    data["count"] = int(data.get("count") or 0) + 1
    data["at"] = datetime.now().astimezone(US_EASTERN).isoformat()
    syms = list(data.get("symbols") or [])
    if symbol and symbol.upper() not in syms:
        syms.append(symbol.upper())
    data["symbols"] = syms[:8]
    return data


async def reserve_entry_slots(
    flags: Any,
    *,
    n: int,
    max_entries: int,
    symbols: list[str] | None = None,
    session: Any = None,
    owner: str | None = None,
    today: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Atomically reserve n daily entry slots. Deny if lock/read/write fails."""
    from services.live_cycle_lock import (
        ENTRY_ADVISORY_KEY,
        FLAG_ENTRY_LOCK,
        acquire_lease,
        release_lease,
        replica_id,
    )

    if n <= 0:
        return False, "no_slots_requested", {}
    who = owner or replica_id()
    try:
        got, lease = await acquire_lease(
            flags,
            owner=who,
            flag=FLAG_ENTRY_LOCK,
            advisory_key=ENTRY_ADVISORY_KEY,
            session=session,
        )
    except Exception as exc:
        logger.warning("live_entry.reserve_lock_failed", error=str(exc))
        return False, "entry_slot_lock_failed", {}
    if not got:
        return False, "entry_slot_lock_held", lease or {}
    try:
        day_flag = await flags.get_json(FLAG_ENTRY_DAY)
        remaining = remaining_entry_slots(day_flag, max_entries=max_entries, today=today)
        if remaining < int(n):
            return False, "max_1_entry_per_day", day_flag
        _, _, day_flag = entry_day_allowed(day_flag, max_entries=max_entries, today=today)
        names = list(symbols or [])
        for i in range(int(n)):
            label = names[i] if i < len(names) else "RESERVED"
            day_flag = record_entry_day_fill(day_flag, label)
        await flags.set_json(FLAG_ENTRY_DAY, day_flag)
        return True, "reserved", day_flag
    except Exception as exc:
        logger.warning("live_entry.reserve_failed", error=str(exc))
        return False, "entry_slot_write_failed", {}
    finally:
        await release_lease(
            flags, who, flag=FLAG_ENTRY_LOCK, advisory_key=ENTRY_ADVISORY_KEY, session=session
        )


def next_et_session_date(day: date | None = None) -> date:
    d = (day or now_et().date()) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def r_multiple_loss(entry: float | None, stop: float | None, exit_px: float | None) -> float | None:
    try:
        e = float(entry or 0)
        s = float(stop or 0)
        x = float(exit_px or 0)
    except (TypeError, ValueError):
        return None
    if e <= 0 or s <= 0 or x <= 0 or e <= s:
        return None
    risk = e - s
    if risk <= 0:
        return None
    return (e - x) / risk


def record_stop_1r_block(
    flag: dict[str, Any] | None,
    symbol: str,
    *,
    r_mult: float,
    today: str | None = None,
) -> dict[str, Any]:
    today = today or et_today()
    stop_day = date.fromisoformat(today)
    until = next_et_session_date(stop_day).isoformat()
    data = dict(flag or {})
    blocks = dict(data.get("symbols") or {})
    blocks[symbol.upper()] = {
        "stop_day": today,
        "until_session": until,
        "r": round(float(r_mult), 3),
    }
    data["symbols"] = blocks
    data["session_blocked"] = True
    data["until_session"] = until
    data["session_symbol"] = symbol.upper()
    data["session_r"] = round(float(r_mult), 3)
    data["at"] = datetime.now().astimezone(US_EASTERN).isoformat()
    return data


def thesis_is_fresh_after_stop(
    flag: dict[str, Any] | None,
    thesis_at: datetime | str | None,
) -> bool:
    """True only when a BUY thesis timestamp is strictly after the 1R stop."""
    if not flag:
        return True
    raw = (flag or {}).get("at")
    if not raw:
        return False
    if thesis_at is None or thesis_at == "":
        return False
    try:
        stop_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        th = thesis_at if isinstance(thesis_at, datetime) else datetime.fromisoformat(
            str(thesis_at).replace("Z", "+00:00")
        )
        if stop_at.tzinfo is None:
            stop_at = stop_at.replace(tzinfo=US_EASTERN)
        if th.tzinfo is None:
            th = th.replace(tzinfo=US_EASTERN)
    except Exception:
        return False
    return th > stop_at


def buy_thesis_blocked(
    flag: dict[str, Any] | None,
    symbol: str,
    *,
    today: str | None = None,
    thesis_at: datetime | str | None = None,
) -> tuple[bool, str]:
    today = today or et_today()
    data = flag or {}
    until = str(data.get("until_session") or "")
    if data.get("session_blocked") and until and today <= until:
        if not thesis_is_fresh_after_stop(data, thesis_at):
            return True, f"stop_1r_session_until_{until}"
    rec = (data.get("symbols") or {}).get((symbol or "").upper())
    if not rec:
        return False, "ok"
    until_sym = str(rec.get("until_session") or until or "")
    if until_sym and today <= until_sym:
        if not thesis_is_fresh_after_stop(data, thesis_at):
            return True, f"stop_1r_skip_until_{until_sym}"
    return False, "ok"


def blocked_buy_symbols(flag: dict[str, Any] | None, *, today: str | None = None) -> set[str]:
    today = today or et_today()
    out: set[str] = set()
    for sym in ((flag or {}).get("symbols") or {}):
        blocked, _ = buy_thesis_blocked(flag, sym, today=today)
        if blocked:
            out.add(sym.upper())
    return out


def submit_fail_pause(
    flag: dict[str, Any] | None,
    *,
    submitted: int,
    failed: int,
    max_fails: int = 3,
    today: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Pause LIVE autopilot entries after N consecutive submit failures. Reset on a fill or new ET day."""
    today = today or et_today()
    data = dict(flag or {})
    if data.get("et_date") != today:
        data = {"et_date": today, "consecutive": 0, "paused": False}
    if int(submitted or 0) > 0:
        data["consecutive"] = 0
        data["paused"] = False
        return False, "reset_on_fill", data
    if int(failed or 0) <= 0:
        return bool(data.get("paused")), "unchanged", data
    data["consecutive"] = int(data.get("consecutive") or 0) + int(failed)
    cap = max(1, int(max_fails or 3))
    if data["consecutive"] >= cap:
        data["paused"] = True
        return True, "submit_fail_pause", data
    return False, "streak", data


def submit_already_paused(flag: dict[str, Any] | None, *, today: str | None = None) -> bool:
    today = today or et_today()
    data = flag or {}
    if data.get("et_date") != today:
        return False
    return bool(data.get("paused"))


async def arm_deposited_brake_if_needed(
    session,
    broker,
    *,
    equity: float | None,
    base: float | None,
    pct: float = 5.0,
    actor: str = "deposited_brake",
) -> dict[str, Any] | None:
    """Arm kill-switch WITHOUT flatten when equity crosses 5% of deposited. Keep brackets."""
    if not deposited_brake_triggered(equity, base, pct):
        return None
    from services.kill_switch_service import KillSwitchService

    svc = KillSwitchService(session, broker)
    if await svc.is_active():
        return {"already_active": True, "flatten": False}
    floor = deposited_brake_floor(float(base or 0), pct)
    reason = (
        f"Freno acumulado {pct:g}% vs depositado: "
        f"equity ${float(equity or 0):.2f} ≤ piso ${floor:.2f} "
        f"(base ${float(base or 0):.2f}). Sin flatten; brackets intactos."
    )
    state = await svc.activate(
        reason=reason,
        actor=actor,
        flatten=False,
        confirm=True,
    )
    return {"armed": True, "flatten": False, "reason": reason, "state": state.model_dump(mode="json")}


ALPACA_MODE_ENV_VARS = ("ALPACA_PAPER", "ALPACA_LIVE_TRADE")


def alpaca_mode_explicit_in_environ(environ: Mapping[str, str] | None = None) -> bool:
    """True when paper vs LIVE was set in the process environment (not a code default)."""
    env = os.environ if environ is None else environ
    for key in ALPACA_MODE_ENV_VARS:
        val = env.get(key)
        if val is not None and str(val).strip() != "":
            return True
    return False


def production_trading_unconfigured(
    settings: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """APP_ENV=production without an explicit ALPACA_PAPER / ALPACA_LIVE_TRADE."""
    if settings is None:
        from config.settings import get_settings

        settings = get_settings()
    env_name = str(getattr(settings, "app_env", "") or "")
    return env_name == "production" and not alpaca_mode_explicit_in_environ(environ)


def trading_mode_label(
    settings: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """paper | live | unconfigured."""
    if settings is None:
        from config.settings import get_settings

        settings = get_settings()
    if production_trading_unconfigured(settings, environ):
        return "unconfigured"
    return "paper" if bool(settings.effective_alpaca_paper) else "live"
