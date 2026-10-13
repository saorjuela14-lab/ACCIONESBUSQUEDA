"""Strategy A ownership vs inherited August lots (PAPER)."""

from __future__ import annotations

from typing import Any

STRATEGY_A_SYMBOLS = frozenset({"BTC/USD", "ETH/USD"})
STRATEGY_A_CID_PREFIX = "sa9-"
STRATEGY_A_META_TAGS = frozenset({"strategy_a", "sa9", "combo9"})
FLAG_STRATEGY_A_ARMED = "crypto_strategy_a_armed"
FLAG_LEGACY_FLAT = "crypto_strategy_a_legacy_flat"

# August 13 lots — Alpaca merges same-symbol qty. BTC/ETH here are inherited, not A.
AUGUST_INHERITED = (
    "ARB/USD",
    "BAT/USD",
    "BCH/USD",
    "BONK/USD",
    "BTC/USD",
    "DOGE/USD",
    "ETH/USD",
    "LDO/USD",
    "RENDER/USD",
    "SOL/USD",
    "SUSHI/USD",
    "WIF/USD",
    "XTZ/USD",
)


def _norm(symbol: str) -> str:
    s = (symbol or "").upper().replace(" ", "")
    if "/" not in s and s.endswith("USD") and len(s) > 3:
        return f"{s[:-3]}/USD"
    return s


def is_strategy_a_symbol(symbol: str) -> bool:
    return _norm(symbol) in STRATEGY_A_SYMBOLS


def is_strategy_a_trade(trade: Any) -> bool:
    """True only for lots the new engine opened (cid prefix or tracker tag)."""
    if trade is None:
        return False
    meta = getattr(trade, "meta", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    tag = str(meta.get("strategy") or meta.get("source") or meta.get("engine") or "").lower()
    if tag in STRATEGY_A_META_TAGS:
        return True
    cid = str(
        meta.get("client_order_id")
        or getattr(trade, "client_order_id", "")
        or ""
    )
    if cid.startswith(STRATEGY_A_CID_PREFIX):
        return True
    return False


def strategy_a_owned(trades: list[Any]) -> list[Any]:
    return [t for t in trades if is_strategy_a_trade(t) and is_strategy_a_symbol(getattr(t, "symbol", ""))]


def inherited_trades(trades: list[Any]) -> list[Any]:
    owned_ids = {id(t) for t in strategy_a_owned(trades)}
    return [t for t in trades if id(t) not in owned_ids]


def inherited_present(trades: list[Any] | None = None, broker_symbols: list[str] | None = None) -> bool:
    if any(not is_strategy_a_trade(t) for t in (trades or []) if getattr(t, "desk", "crypto") == "crypto"):
        return True
    want = {_norm(s) for s in AUGUST_INHERITED}
    for raw in broker_symbols or []:
        if _norm(raw) in want:
            return True
    return False


async def strategy_a_is_armed(
    flags: Any,
    *,
    inherited: bool = False,
) -> bool:
    """Missing flag → DISARMED. ``inherited`` is ignored for the default."""
    del inherited
    if flags is None:
        return False
    raw = await flags.get_json(FLAG_STRATEGY_A_ARMED)
    if not isinstance(raw, dict) or "armed" not in raw:
        return False
    return bool(raw.get("armed"))


async def set_strategy_a_armed(
    flags: Any,
    *,
    armed: bool,
    actor: str,
    allocation_usd: float | None = None,
    equity_usd: float | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    payload = {
        "armed": bool(armed),
        "actor": actor,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if armed and allocation_usd is not None:
        payload["allocation_usd"] = float(allocation_usd)
    if armed and equity_usd is not None:
        payload["equity_at_arm_usd"] = float(equity_usd)
    await flags.set_json(FLAG_STRATEGY_A_ARMED, payload)
    return payload


def armed_allocation_usd(raw: Any) -> float:
    if not isinstance(raw, dict):
        return 0.0
    try:
        return float(raw.get("allocation_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def inherited_on_symbol(trades: list[Any] | None, symbol: str) -> bool:
    want = _norm(symbol)
    for t in trades or []:
        if _norm(str(getattr(t, "symbol", "") or "")) != want:
            continue
        if not is_strategy_a_trade(t):
            return True
    return False


def inherited_btc_eth_symbols(
    trades: list[Any] | None = None,
    broker_symbols: list[str] | None = None,
) -> list[str]:
    found: list[str] = []
    want = STRATEGY_A_SYMBOLS
    for t in trades or []:
        sym = _norm(str(getattr(t, "symbol", "") or ""))
        if sym in want and not is_strategy_a_trade(t) and sym not in found:
            found.append(sym)
    for raw in broker_symbols or []:
        sym = _norm(raw)
        if sym in want and sym not in found:
            # Broker merge: same-symbol lot without an A tag is inherited.
            if not any(
                _norm(str(getattr(t, "symbol", "") or "")) == sym and is_strategy_a_trade(t)
                for t in (trades or [])
            ):
                found.append(sym)
    return found


def desk_actor(scope: Any) -> str:
    """Server-side actor. Never trust a client-supplied name."""
    email = getattr(scope, "email", None)
    if email:
        return str(email)
    user_id = getattr(scope, "user_id", None)
    if user_id:
        return str(user_id)
    return "desk"
