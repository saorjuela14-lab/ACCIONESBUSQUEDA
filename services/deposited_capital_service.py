"""Net deposited capital from Alpaca account activities (reporting base).

Sums cash in minus cash out (CSD / CSW / JNLC / TRANS). Cached with TTL so a
new deposit or withdrawal shows up without a deploy. If Alpaca fails, fall back
to DEPOSITED_BASE_USD — never silently to the $20 trading stamp.
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

TRANSFER_ACTIVITY_TYPES = ("CSD", "CSW", "JNLC", "TRANS")
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
    if typ == "CSW" and amt > 0:
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


async def _paginate(broker: Any, *, activity_types: str | list[str] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(_MAX_PAGES):
        page = await broker.list_account_activities(
            activity_types=activity_types,
            page_size=100,
            page_token=token,
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


async def _fetch_all_transfer_activities(broker: Any) -> list[dict[str, Any]]:
    """Prefer per-type paths (CSD/CSW/JNLC/TRANS); fall back to unfiltered list."""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for typ in TRANSFER_ACTIVITY_TYPES:
        try:
            page = await _paginate(broker, activity_types=typ)
        except Exception as exc:
            errors.append(f"{typ}:{exc}")
            logger.warning("deposited_capital.type_failed", activity_type=typ, error=str(exc))
            continue
        for row in page:
            rid = str((row or {}).get("id") or "")
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            collected.append(row)

    if collected:
        return collected

    try:
        unfiltered = await _paginate(broker, activity_types=None)
    except Exception as exc:
        errors.append(f"all:{exc}")
        logger.warning("deposited_capital.unfiltered_failed", error=str(exc))
        if errors:
            raise RuntimeError("; ".join(errors[:4])) from exc
        raise
    kinds: dict[str, int] = {}
    for row in unfiltered:
        typ = str((row or {}).get("activity_type") or (row or {}).get("type") or "?")
        kinds[typ] = kinds.get(typ, 0) + 1
    logger.info("deposited_capital.unfiltered_types", types=kinds, n=len(unfiltered))
    return unfiltered


async def _from_alpaca() -> DepositedBase | None:
    broker = get_broker_provider()
    if not broker.is_configured():
        return None
    rows = await _fetch_all_transfer_activities(broker)
    net, deposits, withdrawals, counted = net_transfers_from_activities(rows)
    if counted <= 0 or net <= 0:
        logger.warning(
            "deposited_capital.empty_activities",
            counted=counted,
            net=net,
        )
        return None
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
            snap = None
        if snap is not None and snap.amount and snap.amount > 0:
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
        logger.error("deposited_capital.unavailable")
        return DepositedBase(amount=None, source="unavailable")
