"""Aggressive-but-capped paper risk for the multi-asset desk. Never LIVE equity.

1x only (no margin). Volatility sizing 2–3% risk per trade. Mandatory stop.
Trailing after +1R. Daily + weekly loss caps. Own drawdown kill-switch.
Crypto 24/7: calendar-day loss (weekends included). Strategy A stops are
software-only at each 4h close — Alpaca crypto does not accept stop/bracket/OCO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from config.settings import get_settings
from database.repositories.ops_repository import OpsFlagRepository
from domain.multiasset import AssetDeskId
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)

DeskId = AssetDeskId
FLAG_KILL = "multiasset_kill_switch"
FLAG_PNL = "multiasset_pnl_window"
FLAG_CYCLE = "multiasset_last_cycle"

# Defaults — overridable via settings. More aggressive than the ~1% equity micro book.
DEFAULT_RISK_PCT = {"gold": 2.5, "forex": 2.5, "crypto": 3.0}


@dataclass
class MultiAssetRiskPolicy:
    risk_pct: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RISK_PCT))
    max_daily_loss_pct: float = 6.0
    max_weekly_loss_pct: float = 12.0
    max_drawdown_pct: float = 20.0
    trail_arm_r: float = 1.0
    allow_leverage: bool = False
    max_leverage: float = 1.0
    crypto_broker_stops: bool = False
    crypto_24_7_monitor: bool = True


def policy_from_settings() -> MultiAssetRiskPolicy:
    s = get_settings()
    return MultiAssetRiskPolicy(
        risk_pct={
            "gold": float(getattr(s, "multiasset_risk_pct_gold", 2.5) or 2.5),
            "forex": float(getattr(s, "multiasset_risk_pct_forex", 2.5) or 2.5),
            "crypto": float(getattr(s, "multiasset_risk_pct_crypto", 3.0) or 3.0),
        },
        max_daily_loss_pct=float(getattr(s, "multiasset_max_daily_loss_pct", 6.0) or 6.0),
        max_weekly_loss_pct=float(getattr(s, "multiasset_max_weekly_loss_pct", 12.0) or 12.0),
        max_drawdown_pct=float(getattr(s, "multiasset_max_drawdown_pct", 20.0) or 20.0),
        trail_arm_r=float(getattr(s, "multiasset_trail_arm_r", 1.0) or 1.0),
        allow_leverage=bool(getattr(s, "multiasset_allow_leverage", False)),
        max_leverage=1.0 if not bool(getattr(s, "multiasset_allow_leverage", False)) else float(
            getattr(s, "multiasset_max_leverage", 1.0) or 1.0
        ),
        crypto_broker_stops=False,
        crypto_24_7_monitor=True,
    )


def size_notional_1x(
    *,
    equity: float,
    cash: float,
    desk_budget: float,
    entry: float,
    stop: float,
    risk_pct: float,
    open_notional: float = 0.0,
    max_leverage: float = 1.0,
) -> tuple[float, dict[str, Any]]:
    """Dollar risk / stop distance, then cap at cash and 1× equity. Never sizes above equity."""
    info: dict[str, Any] = {"leverage_cap": max_leverage, "risk_pct": risk_pct}
    if entry <= 0 or stop <= 0 or stop >= entry:
        info["reason"] = "invalid_stop"
        return 0.0, info
    stop_dist = entry - stop
    risk_usd = max(0.0, desk_budget) * (risk_pct / 100.0)
    raw = risk_usd / stop_dist * entry if stop_dist > 0 else 0.0
    room_desk = max(0.0, desk_budget - open_notional)
    cap_lev = max(0.0, equity * max_leverage - open_notional) if equity > 0 else 0.0
    notional = min(raw, room_desk, cash, cap_lev)
    if notional < 15:
        info["reason"] = "too_small"
        return 0.0, info
    info["stop_pct"] = round(stop_dist / entry * 100.0, 3)
    info["stop_r"] = 1.0
    info["raw_notional"] = round(raw, 2)
    return round(notional, 2), info


def trail_stop(
    *,
    entry: float,
    stop: float,
    peak: float,
    price: float,
    trail_atr_abs: float,
    arm_r: float = 1.0,
) -> tuple[float, bool]:
    """Ratchet stop up only. Arms after +arm_r × initial risk."""
    if entry <= 0 or stop <= 0 or stop >= entry:
        return stop, False
    risk = entry - stop
    peak = max(peak, price, entry)
    armed = (peak - entry) >= (arm_r * risk - 1e-9)
    if not armed or trail_atr_abs <= 0:
        return stop, False
    candidate = peak - trail_atr_abs
    if candidate > stop:
        return round(candidate, 6), True
    return stop, False


def calendar_day_key(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def iso_week_key(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    y, w, _ = dt.astimezone(timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


class MultiAssetRiskDesk:
    """Persistent paper-only gates. Distinct from the LIVE equity kill-switch."""

    def __init__(self, session: AsyncSession | None) -> None:
        self._session = session
        self._policy = policy_from_settings()

    @property
    def policy(self) -> MultiAssetRiskPolicy:
        return self._policy

    async def snapshot(self, *, equity: float, peak_equity: float | None = None) -> dict[str, Any]:
        kill = await self.kill_state()
        pnl = await self._pnl_window()
        peak = float(peak_equity or pnl.get("peak_equity") or equity or 0)
        dd = 0.0
        if peak > 0 and equity > 0:
            dd = max(0.0, (peak - equity) / peak * 100.0)
        return {
            "paper": True,
            "live_equity_untouched": True,
            "leverage": 1.0 if not self._policy.allow_leverage else self._policy.max_leverage,
            "allow_leverage": self._policy.allow_leverage,
            "risk_pct": self._policy.risk_pct,
            "max_daily_loss_pct": self._policy.max_daily_loss_pct,
            "max_weekly_loss_pct": self._policy.max_weekly_loss_pct,
            "max_drawdown_pct": self._policy.max_drawdown_pct,
            "drawdown_pct": round(dd, 2),
            "kill_switch": kill,
            "day_key": pnl.get("day_key"),
            "day_pnl_pct": pnl.get("day_pnl_pct"),
            "week_pnl_pct": pnl.get("week_pnl_pct"),
            "crypto_24_7": {
                "broker_stops_gtc": False,
                "broker_stop": "none",
                "daily_loss_includes_weekend": True,
                "autopilot_24_7": self._policy.crypto_24_7_monitor,
                "note": (
                    "Alpaca crypto solo acepta market/limit/stop_limit; "
                    "Strategy A no envía stops al broker. Protección = chandelier "
                    "al cierre de cada vela 4h (paper no modela bien los gaps)."
                ),
            },
        }

    async def kill_state(self) -> dict[str, Any]:
        if self._session is None:
            return {"active": False, "reason": None}
        data = await OpsFlagRepository(self._session).get_json(FLAG_KILL)
        return {
            "active": bool(data.get("active")),
            "reason": data.get("reason"),
            "at": data.get("at"),
            "drawdown_pct": data.get("drawdown_pct"),
        }

    async def set_kill(self, *, active: bool, reason: str, drawdown_pct: float | None = None) -> None:
        if self._session is None:
            return
        await OpsFlagRepository(self._session).set_json(
            FLAG_KILL,
            {
                "active": active,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
                "drawdown_pct": drawdown_pct,
                "paper_only": True,
            },
        )
        logger.warning("multiasset.kill", active=active, reason=reason, dd=drawdown_pct)

    async def _pnl_window(self) -> dict[str, Any]:
        if self._session is None:
            return {}
        return await OpsFlagRepository(self._session).get_json(FLAG_PNL)

    async def update_equity_mark(self, equity: float) -> dict[str, Any]:
        """Mark paper equity for daily/weekly/DD. Weekends count (crypto 24/7)."""
        if self._session is None or equity <= 0:
            return {}
        repo = OpsFlagRepository(self._session)
        data = await repo.get_json(FLAG_PNL)
        day = calendar_day_key()
        week = iso_week_key()
        if data.get("day_key") != day:
            data["day_key"] = day
            data["day_start_equity"] = equity
            data["day_pnl_pct"] = 0.0
        else:
            start = float(data.get("day_start_equity") or equity)
            data["day_pnl_pct"] = round((equity - start) / start * 100.0, 3) if start else 0.0
        if data.get("week_key") != week:
            data["week_key"] = week
            data["week_start_equity"] = equity
            data["week_pnl_pct"] = 0.0
        else:
            wstart = float(data.get("week_start_equity") or equity)
            data["week_pnl_pct"] = round((equity - wstart) / wstart * 100.0, 3) if wstart else 0.0
        peak = max(float(data.get("peak_equity") or 0), equity)
        data["peak_equity"] = peak
        data["equity"] = equity
        dd = (peak - equity) / peak * 100.0 if peak else 0.0
        data["drawdown_pct"] = round(dd, 3)
        await repo.set_json(FLAG_PNL, data)

        if dd >= self._policy.max_drawdown_pct:
            await self.set_kill(
                active=True,
                reason=f"drawdown {dd:.1f}% ≥ {self._policy.max_drawdown_pct:.0f}%",
                drawdown_pct=dd,
            )
        return data

    async def block_new_buys(self, equity: float) -> tuple[bool, str | None]:
        kill = await self.kill_state()
        if kill.get("active"):
            return True, "multiasset_kill_switch"
        data = await self.update_equity_mark(equity)
        day_pct = float(data.get("day_pnl_pct") or 0)
        week_pct = float(data.get("week_pnl_pct") or 0)
        if day_pct <= -abs(self._policy.max_daily_loss_pct):
            return True, f"daily_loss {day_pct:.1f}%"
        if week_pct <= -abs(self._policy.max_weekly_loss_pct):
            return True, f"weekly_loss {week_pct:.1f}%"
        return False, None

    async def record_cycle(self, payload: dict[str, Any]) -> None:
        if self._session is None:
            return
        await OpsFlagRepository(self._session).set_json(
            FLAG_CYCLE,
            {**payload, "at": datetime.now(timezone.utc).isoformat(), "paper": True},
        )
