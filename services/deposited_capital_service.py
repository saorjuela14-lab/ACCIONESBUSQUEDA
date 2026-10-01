"""Net deposited capital from Alpaca account activities.

Used as the reporting P&L base AND the trading/risk/sizing denominator
(bootstrap, reconcile, sync-Alpaca, initial_capital lock, VaR $, kill-switch
and drawdown %). Cached with TTL. If Alpaca fails, fall back to
DEPOSITED_BASE_USD, then last cache, then conservative equity — never
silently to $20.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from config.settings import get_settings
from providers.broker.factory import get_broker_provider
from utils.logging import get_logger

logger = get_logger(__name__)

TRANSFER_ACTIVITY_TYPES = ("CSD", "CSW", "JNLC", "TRANS", "OCT", "ACATC", "FOPT")
_SKIP_STATUS = frozenset({"canceled", "cancelled", "pending", "rejected", "queued", "failed"})
_MAX_PAGES = 50


@dataclass(frozen=True)
class DepositedBase:
    amount: float | None
    source: str  # alpaca | cache | env | unavailable
    deposits: float = 0.0
    withdrawals: float = 0.0
    activity_count: int = 0


_lock = asyncio.Lock()
_cache: DepositedBase | None = None
_cache_at: float = 0.0


def reset_deposited_cache() -> None:
    """Test helper — drop in-memory TTL cache."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def activity_signed_amount(row: dict[str, Any]) -> float:
    """Signed cash flow: deposits positive, withdrawals negative."""
    typ = str(row.get("activity_type") or row.get("type") or "").upper()
    raw = row.get("net_amount")
    if raw in (None, ""):
        raw = row.get("amount")
    amt = _f(raw)
    if amt == 0:
        qty = _f(row.get("qty"))
        price = _f(row.get("price") or row.get("per_share_amount"))
        if qty and price:
            amt = qty * price
        elif typ == "OCT" and qty:
            # On-chain USDT/USDC often stores notional in qty with net_amount=0
            amt = qty
        else:
            for key in ("usd_value", "notional", "cash"):
                extra = _f(row.get(key))
                if extra:
                    amt = extra
                    break
    if typ in {"CSW"} and amt > 0:
        return -amt
    return amt


def net_transfers_from_activities(rows: list[dict[str, Any]]) -> tuple[float, float, float, int]:
    """Return (net, deposits, withdrawals_abs, counted)."""
    deposits = 0.0
    withdrawals = 0.0
    counted = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "executed").lower()
        if status in _SKIP_STATUS:
            continue
        typ = str(row.get("activity_type") or row.get("type") or "").upper()
        if typ and typ not in TRANSFER_ACTIVITY_TYPES:
            continue
        signed = activity_signed_amount(row)
        if signed == 0:
            continue
        counted += 1
        if signed > 0:
            deposits += signed
        else:
            withdrawals += abs(signed)
    net = round(deposits - withdrawals, 2)
    return net, round(deposits, 2), round(withdrawals, 2), counted


def _env_fallback() -> DepositedBase | None:
    raw = get_settings().deposited_base_usd
    if raw is None:
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return DepositedBase(amount=round(amount, 2), source="env")


async def _paginate(
    broker: Any,
    *,
    activity_types: str | list[str] | None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(_MAX_PAGES):
        page = await broker.list_account_activities(
            activity_types=activity_types,
            page_size=100,
            page_token=token,
            category=category,
        )
        if not page:
            break
        out.extend(page)
        if len(page) < 100:
            break
        token = str(page[-1].get("id") or "") or None
        if not token:
            break
    return out


def _row_sample(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        typ = str((row or {}).get("activity_type") or (row or {}).get("type") or "").upper()
        if typ in TRANSFER_ACTIVITY_TYPES:
            bits = []
            for key in (
                "activity_type",
                "net_amount",
                "amount",
                "qty",
                "price",
                "symbol",
                "description",
                "status",
            ):
                val = row.get(key)
                if val not in (None, ""):
                    bits.append(f"{key}={val}")
            bits.append("keys=" + ",".join(sorted(str(k) for k in row)[:12]))
            return " ".join(bits)[:140]
    if rows:
        return "keys=" + ",".join(sorted(str(k) for k in rows[0])[:12])
    return ""


def _type_histogram(rows: list[dict[str, Any]]) -> str:
    kinds: dict[str, int] = {}
    for row in rows:
        typ = str((row or {}).get("activity_type") or (row or {}).get("type") or "?")
        kinds[typ] = kinds.get(typ, 0) + 1
    if not kinds:
        return "none"
    return ",".join(f"{k}:{v}" for k, v in list(kinds.items())[:8])


async def _fetch_all_transfer_activities(broker: Any) -> list[dict[str, Any]]:
    """Per-type CSD/CSW/JNLC/TRANS (non_trade), then unfiltered ledger."""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for typ in TRANSFER_ACTIVITY_TYPES:
        for category in ("non_trade", None):
            try:
                page = await _paginate(broker, activity_types=typ, category=category)
            except Exception as exc:
                errors.append(f"{typ}:{exc}")
                logger.warning(
                    "deposited_capital.type_failed",
                    activity_type=typ,
                    category=category,
                    error=str(exc),
                )
                continue
            for row in page:
                rid = str((row or {}).get("id") or "")
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                collected.append(row)
            if page:
                break

    if collected:
        return collected

    for category in ("non_trade", None):
        try:
            unfiltered = await _paginate(broker, activity_types=None, category=category)
        except Exception as exc:
            errors.append(f"all:{exc}")
            logger.warning("deposited_capital.unfiltered_failed", error=str(exc))
            continue
        logger.info(
            "deposited_capital.unfiltered_types",
            types=_type_histogram(unfiltered),
            n=len(unfiltered),
            category=category,
        )
        if unfiltered:
            return unfiltered
    if errors:
        raise RuntimeError("; ".join(errors[:4]))
    return []


async def _from_alpaca() -> DepositedBase:
    broker = get_broker_provider()
    if not broker.is_configured():
        return DepositedBase(amount=None, source="unavailable:alpaca_unconfigured")
    rows = await _fetch_all_transfer_activities(broker)
    net, deposits, withdrawals, counted = net_transfers_from_activities(rows)
    if counted <= 0 or net <= 0:
        hint = _type_histogram(rows)
        sample = _row_sample(rows)
        logger.warning(
            "deposited_capital.empty_activities",
            counted=counted,
            net=net,
            types=hint,
            sample=sample,
        )
        suffix = f"{hint} {sample}".strip()
        return DepositedBase(
            amount=None,
            source=f"unavailable:empty:{suffix}"[:160],
            deposits=deposits,
            withdrawals=withdrawals,
            activity_count=counted,
        )
    return DepositedBase(
        amount=net,
        source="alpaca",
        deposits=deposits,
        withdrawals=withdrawals,
        activity_count=counted,
    )


async def get_deposited_base(*, force: bool = False) -> DepositedBase:
    """Reporting denominator: Alpaca net deposits, else env, else unavailable.

    Never returns the silent $20 trading stamp.
    """
    global _cache, _cache_at
    ttl = float(get_settings().deposited_base_cache_ttl_seconds or 600)
    now = time.monotonic()
    if (
        not force
        and _cache is not None
        and _cache.source == "alpaca"
        and _cache.amount
        and (now - _cache_at) < ttl
    ):
        return _cache

    async with _lock:
        now = time.monotonic()
        if (
            not force
            and _cache is not None
            and _cache.source == "alpaca"
            and _cache.amount
            and (now - _cache_at) < ttl
        ):
            return _cache
        try:
            snap = await _from_alpaca()
        except Exception as exc:
            logger.warning("deposited_capital.alpaca_failed", error=str(exc))
            snap = DepositedBase(amount=None, source=f"unavailable:{exc}"[:80])
        if snap.amount and snap.amount > 0:
            _cache = snap
            _cache_at = time.monotonic()
            logger.info(
                "deposited_capital.alpaca",
                amount=snap.amount,
                deposits=snap.deposits,
                withdrawals=snap.withdrawals,
                activities=snap.activity_count,
            )
            return snap
        if _cache is not None and _cache.amount and _cache.amount > 0:
            stale = DepositedBase(
                amount=_cache.amount,
                source="cache",
                deposits=_cache.deposits,
                withdrawals=_cache.withdrawals,
                activity_count=_cache.activity_count,
            )
            logger.warning("deposited_capital.stale_cache", amount=stale.amount)
            return stale
        env = _env_fallback()
        if env is not None:
            logger.warning("deposited_capital.env_fallback", amount=env.amount)
            return env
        logger.error("deposited_capital.unavailable", source=snap.source if snap else None)
        return snap if snap is not None else DepositedBase(amount=None, source="unavailable")


async def resolve_trading_base(*, equity: float | None = None) -> DepositedBase:
    """Denominator for sizing/risk/DB stamp.

    alpaca → cache → env → min(equity, last known) → equity → unavailable.
    Never invents $20.
    """
    snap = await get_deposited_base()
    src = str(snap.source or "")
    # Fresh Alpaca or explicit env override are the real deposited denominator.
    if snap.amount and snap.amount > 0 and src in ("alpaca", "env", "cache"):
        return snap
    last = _cache
    eq = float(equity or 0.0)
    last_amt = float(last.amount) if last is not None and last.amount and last.amount > 0 else 0.0
    if snap.amount and snap.amount > 0:
        last_amt = max(last_amt, float(snap.amount))
        last = snap
    if last_amt > 0 and eq > 0:
        amt = round(min(eq, last_amt), 2)
        logger.warning("trading_base.conservative_min", amount=amt, equity=eq, last=last_amt)
        return DepositedBase(
            amount=amt,
            source="conservative:min_equity_cache",
            deposits=last.deposits if last else 0.0,
            withdrawals=last.withdrawals if last else 0.0,
            activity_count=last.activity_count if last else 0,
        )
    if last_amt > 0:
        logger.warning("trading_base.conservative_cache", amount=last_amt)
        return DepositedBase(
            amount=round(last_amt, 2),
            source="cache",
            deposits=last.deposits if last else 0.0,
            withdrawals=last.withdrawals if last else 0.0,
            activity_count=last.activity_count if last else 0,
        )
    if eq > 0:
        logger.warning("trading_base.conservative_equity", amount=round(eq, 2))
        return DepositedBase(amount=round(eq, 2), source="conservative:equity")
    return snap if snap is not None else DepositedBase(amount=None, source="unavailable")
