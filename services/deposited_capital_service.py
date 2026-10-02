"""Single deposited base for P&L, brake/kill, dashboard and initial_capital.

Canonical value: DEPOSITED_BASE_USD (Sergio: 21.76; 5% floor = 20.67).
Alpaca activities and portfolios.initial_capital are comparison sources only.
If the env var is missing, buys fail closed — never invent $20 or use equity.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any

from config.settings import get_settings
from providers.broker.factory import get_broker_provider
from utils.logging import get_logger

logger = get_logger(__name__)

TRANSFER_ACTIVITY_TYPES = ("CSD", "CSW", "JNLC", "TRANS", "OCT", "ACATC", "FOPT")
_SKIP_STATUS = frozenset({"canceled", "cancelled", "pending", "rejected", "queued", "failed"})
_MAX_PAGES = 50
CANONICAL_SOURCE = "env:DEPOSITED_BASE_USD"
MISSING_SOURCE = "missing:DEPOSITED_BASE_USD"
DISCREPANCY_USD = 0.02


@dataclass(frozen=True)
class DepositedBase:
    amount: float | None
    source: str  # env:DEPOSITED_BASE_USD | missing:DEPOSITED_BASE_USD
    deposits: float = 0.0
    withdrawals: float = 0.0
    activity_count: int = 0
    floor_5pct: float | None = None
    buy_allowed: bool = True
    warnings: tuple[str, ...] = ()
    alpaca_amount: float | None = None
    portfolio_initial: float | None = None


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


def _env_amount() -> float | None:
    raw = get_settings().deposited_base_usd
    if raw is None:
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return round(amount, 2)


def _floor_5pct(amount: float | None, pct: float = 5.0) -> float | None:
    if amount is None or float(amount) <= 0:
        return None
    return round(float(amount) * (1.0 - abs(float(pct)) / 100.0), 2)


def _env_fallback() -> DepositedBase | None:
    """Back-compat helper — prefer get_deposited_base()."""
    amount = _env_amount()
    if amount is None:
        return None
    return DepositedBase(
        amount=amount,
        source=CANONICAL_SOURCE,
        floor_5pct=_floor_5pct(amount),
        buy_allowed=True,
    )


def deposited_base_status(snap: DepositedBase) -> dict[str, Any]:
    """ops/status payload: value, source, floor, warnings."""
    return {
        "amount": snap.amount,
        "source": snap.source,
        "floor_5pct": snap.floor_5pct,
        "buy_allowed": bool(snap.buy_allowed),
        "warnings": list(snap.warnings or ()),
        "alpaca_amount": snap.alpaca_amount,
        "portfolio_initial": snap.portfolio_initial,
    }


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


def _with_portfolio(snap: DepositedBase, portfolio_initial: float | None) -> DepositedBase:
    if portfolio_initial is None:
        return snap
    try:
        pf = float(portfolio_initial)
    except (TypeError, ValueError):
        return snap
    if pf <= 0:
        return replace(snap, portfolio_initial=None)
    warns = list(snap.warnings or ())
    if snap.amount and abs(pf - float(snap.amount)) > DISCREPANCY_USD:
        warns.append(
            f"discrepancy portfolios.initial_capital={pf:.2f} vs {snap.source}={float(snap.amount):.2f}"
        )
    return replace(snap, portfolio_initial=round(pf, 2), warnings=tuple(warns))


async def get_deposited_base(
    *,
    force: bool = False,
    portfolio_initial: float | None = None,
) -> DepositedBase:
    """Canonical deposited base: DEPOSITED_BASE_USD only.

    Alpaca / portfolio stamps are compared for warnings. Missing env →
    amount=None and buy_allowed=False.
    """
    global _cache, _cache_at
    ttl = float(get_settings().deposited_base_cache_ttl_seconds or 600)
    now = time.monotonic()
    if not force and _cache is not None and (now - _cache_at) < ttl:
        return _with_portfolio(_cache, portfolio_initial)

    async with _lock:
        now = time.monotonic()
        if not force and _cache is not None and (now - _cache_at) < ttl:
            return _with_portfolio(_cache, portfolio_initial)

        env_amt = _env_amount()
        pct = float(getattr(get_settings(), "deposited_brake_pct", 5.0) or 5.0)
        alpaca_amt = None
        deposits = 0.0
        withdrawals = 0.0
        counted = 0
        try:
            alp = await _from_alpaca()
            alpaca_amt = alp.amount if alp.amount and alp.amount > 0 else None
            deposits = alp.deposits
            withdrawals = alp.withdrawals
            counted = alp.activity_count
        except Exception as exc:
            logger.warning("deposited_capital.alpaca_compare_failed", error=str(exc))

        warnings: list[str] = []
        if env_amt is None:
            warnings.append("DEPOSITED_BASE_USD missing — compras fail-closed")
            if alpaca_amt:
                warnings.append(
                    f"alpaca_observed={alpaca_amt:.2f} ignored (canonical is DEPOSITED_BASE_USD)"
                )
            snap = DepositedBase(
                amount=None,
                source=MISSING_SOURCE,
                deposits=deposits,
                withdrawals=withdrawals,
                activity_count=counted,
                floor_5pct=None,
                buy_allowed=False,
                warnings=tuple(warnings),
                alpaca_amount=alpaca_amt,
            )
            logger.error("deposited_capital.missing_env", alpaca=alpaca_amt)
            _cache = snap
            _cache_at = time.monotonic()
            return _with_portfolio(snap, portfolio_initial)

        if alpaca_amt is not None and abs(float(alpaca_amt) - env_amt) > DISCREPANCY_USD:
            warnings.append(
                f"discrepancy alpaca={float(alpaca_amt):.2f} vs {CANONICAL_SOURCE}={env_amt:.2f}"
            )
        snap = DepositedBase(
            amount=env_amt,
            source=CANONICAL_SOURCE,
            deposits=deposits,
            withdrawals=withdrawals,
            activity_count=counted,
            floor_5pct=_floor_5pct(env_amt, pct),
            buy_allowed=True,
            warnings=tuple(warnings),
            alpaca_amount=alpaca_amt,
        )
        _cache = snap
        _cache_at = time.monotonic()
        logger.info(
            "deposited_capital.canonical",
            amount=snap.amount,
            source=snap.source,
            floor_5pct=snap.floor_5pct,
            alpaca=alpaca_amt,
            warnings=list(snap.warnings),
        )
        return _with_portfolio(snap, portfolio_initial)


async def resolve_trading_base(
    *,
    equity: float | None = None,
    portfolio_initial: float | None = None,
) -> DepositedBase:
    """Same as get_deposited_base — single deposited denominator. equity unused."""
    del equity
    return await get_deposited_base(portfolio_initial=portfolio_initial)
