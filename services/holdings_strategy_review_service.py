"""Continuous holdings strategy review — reformulate thesis, prefer take-profit exits.

Open positions are not "set and forget". Every autopilot cycle re-reads the market,
updates stop/TP levels when the thesis still holds, and flips to exit when the
committee/technical desk says sell or when price is in the take-profit zone.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from agents.technical.indicators import (
    build_trade_levels,
    detect_support_resistance,
    enrich_indicators,
)
from config.settings import get_settings
from database.repositories.ops_repository import PositionMandateRepository
from domain.ops import PositionMandate, utc_now
from providers.market.factory import get_market_provider
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from services.position_lifecycle_service import PositionLifecycleService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)

_USD_PARKING = frozenset({"USDTUSD", "USDT/USD", "USDCUSD", "USDC/USD"})


def _is_parking(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return sym in _USD_PARKING or sym.replace("/", "") in {"USDTUSD", "USDCUSD"}


class HoldingsStrategyReviewService:
    def __init__(
        self,
        session: AsyncSession,
        broker: AlpacaOrderService | None = None,
        analysis: Any | None = None,
    ) -> None:
        self._session = session
        self._broker = broker or AlpacaOrderService()
        self._market = get_market_provider()
        self._mandates = PositionMandateRepository(session)
        self._life = PositionLifecycleService(session, self._broker)
        self._audit = AuditService(session)
        self._settings = get_settings()
        self._analysis = analysis

    async def review(self, *, execute_exits: bool = True) -> dict[str, Any]:
        if not self._settings.holdings_strategy_review_enabled:
            return {"skipped": True, "reason": "disabled"}

        mandates = await self._life.sync_mandates_from_broker()
        active = [m for m in mandates if not _is_parking(m.symbol)]
        if not active:
            return {"reviewed": 0, "updates": [], "exits": [], "warnings": []}

        # Cap concurrency — keep under host timeout budget
        max_n = max(1, int(self._settings.holdings_review_max_positions))
        targets = active[:max_n]
        sem = asyncio.Semaphore(max(1, int(self._settings.holdings_review_concurrency)))

        updates: list[dict[str, Any]] = []
        exits: list[str] = []
        warnings: list[str] = []

        async def _one(m: PositionMandate) -> None:
            async with sem:
                try:
                    result = await self._review_one(m, execute_exits=execute_exits)
                    if result.get("update"):
                        updates.append(result["update"])
                    if result.get("exited"):
                        exits.append(m.symbol)
                    if result.get("warning"):
                        warnings.append(str(result["warning"]))
                except Exception as exc:
                    warnings.append(f"{m.symbol}: review failed ({exc})")
                    logger.warning("holdings.review_failed", symbol=m.symbol, error=str(exc))

        await asyncio.gather(*[_one(m) for m in targets])
        return {
            "reviewed": len(targets),
            "updates": updates,
            "exits": exits,
            "warnings": warnings[:8],
        }

    async def _review_one(
        self,
        mandate: PositionMandate,
        *,
        execute_exits: bool,
    ) -> dict[str, Any]:
        snap = await self._technical_snapshot(mandate.symbol)
        price = float(snap["price"] or 0)
        if price <= 0:
            return {"warning": f"{mandate.symbol}: sin precio"}

        levels = snap["levels"]
        bias = snap["bias"]  # buy | hold | sell
        rsi = snap.get("rsi")
        chg5 = snap.get("chg5")

        # Prefer committee micro score when available (richer reformulation)
        rec = bias
        thesis_txt = snap.get("thesis") or ""
        if self._analysis is not None and hasattr(self._analysis, "score_for_micro_consensus"):
            try:
                thesis = await self._analysis.score_for_micro_consensus(mandate.symbol)
                rec = (thesis.recommendation.value if thesis.recommendation else bias) or bias
                thesis_txt = (
                    (thesis.executive_summary or thesis.investment_thesis or "")[:220]
                    or thesis_txt
                )
            except Exception as exc:
                logger.info(
                    "holdings.committee_skip",
                    symbol=mandate.symbol,
                    error=str(exc),
                )

        entry = float(mandate.entry_price or 0)
        pnl_pct = ((price / entry) - 1.0) * 100 if entry > 0 else 0.0
        new_stop = levels.get("stop_loss")
        new_tp = levels.get("take_profit_1") or levels.get("take_profit")

        # Overnight carry that recovered → bank the rebound (last week's lesson)
        opened = mandate.opened_at
        is_overnight = False
        if opened is not None:
            from datetime import timezone

            from utils.market_hours import US_EASTERN, now_et

            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            is_overnight = opened.astimezone(US_EASTERN).date() < now_et().date()
        recovery_harvest = (
            is_overnight
            and pnl_pct >= float(self._settings.intraday_flat_min_pnl_pct)
            and rec in ("buy", "hold", "strong_buy")
        )

        # --- Sell / invalidate: strategy says leave ---
        if rec in ("sell", "strong_sell"):
            reason = f"Revisión continua: {rec.upper()} · {thesis_txt[:160]}"
            await self._life.invalidate_thesis(mandate.symbol, reason)
            exited = False
            if execute_exits:
                report = await self._life.scan(execute_exits=True)
                exited = mandate.symbol in (report.exits or [])
            return {
                "exited": exited,
                "update": {
                    "symbol": mandate.symbol,
                    "action": "invalidate_exit",
                    "recommendation": rec,
                    "price": price,
                    "pnl_pct": round(pnl_pct, 2),
                },
            }

        # --- Take-profit zone: lock gains when near target or thesis says harvest ---
        tp = float(mandate.take_profit or 0) or float(new_tp or 0)
        near_tp = tp > 0 and price >= tp * float(self._settings.holdings_tp_near_pct)
        harvest = (
            near_tp
            and pnl_pct >= float(self._settings.holdings_min_tp_pnl_pct)
            and rec in ("buy", "hold", "strong_buy")
        )
        # Explicit harvest: in profit and momentum fading (RSI overbought / 5d fade)
        fade = (
            pnl_pct >= float(self._settings.holdings_min_tp_pnl_pct)
            and (
                (rsi is not None and rsi >= 72)
                or (chg5 is not None and chg5 < -3 and pnl_pct > 0)
            )
            and rec == "hold"
        )
        if harvest or fade or recovery_harvest:
            reason = (
                (
                    f"Recuperación overnight → asegurar @ {price:.4f} · PnL {pnl_pct:+.1f}%"
                    if recovery_harvest and not (harvest or fade)
                    else (
                        f"Take-profit estratégico @ {price:.4f}"
                        + (f" (cerca TP {tp:.4f})" if near_tp else "")
                        + f" · PnL {pnl_pct:+.1f}% · bias={rec}"
                    )
                )
            )
            await self._life.invalidate_thesis(mandate.symbol, reason)
            exited = False
            if execute_exits:
                report = await self._life.scan(execute_exits=True)
                exited = mandate.symbol in (report.exits or [])
            await self._audit.record(
                "thesis_reformulate",
                symbol=mandate.symbol,
                message=reason,
                actor="holdings_review",
                payload={
                    "recommendation": rec,
                    "price": price,
                    "pnl_pct": pnl_pct,
                    "overnight_recovery": recovery_harvest,
                },
            )
            return {
                "exited": exited,
                "update": {
                    "symbol": mandate.symbol,
                    "action": "overnight_recovery" if recovery_harvest and not (harvest or fade) else "take_profit",
                    "recommendation": rec,
                    "price": price,
                    "pnl_pct": round(pnl_pct, 2),
                },
            }

        # --- Still valid: reformulate levels (raise TP / tighten stop) ---
        m = await self._mandates.get_open(mandate.symbol) or mandate
        changed = False
        old_stop, old_tp = m.stop_loss, m.take_profit

        if new_stop and new_stop > 0:
            # Only tighten (never loosen) protective stop
            if m.stop_loss is None or new_stop > float(m.stop_loss):
                # Cap stop below market
                if new_stop < price * 0.995:
                    m.stop_loss = round(float(new_stop), 4)
                    changed = True

        if new_tp and new_tp > 0 and rec in ("buy", "strong_buy", "hold"):
            # Raise take-profit when strategy still supports the trade
            if m.take_profit is None or new_tp > float(m.take_profit):
                if new_tp > price * 1.005:
                    m.take_profit = round(float(new_tp), 4)
                    changed = True

        if thesis_txt:
            m.thesis = thesis_txt[:400]
            changed = True
        m.last_checked_at = utc_now()

        if changed:
            await self._mandates.save(m)
            broker_detail = None
            if m.stop_loss and (old_stop is None or float(m.stop_loss) > float(old_stop or 0)):
                broker_detail = await self._life._sync_broker_stop(m, m.stop_loss)
            msg = (
                f"Tesis reformulada ({rec}): stop {old_stop}→{m.stop_loss}, "
                f"TP {old_tp}→{m.take_profit}"
                + (f" · {broker_detail}" if broker_detail else "")
            )
            await self._audit.record(
                "thesis_reformulate",
                symbol=m.symbol,
                message=msg,
                actor="holdings_review",
                payload={
                    "recommendation": rec,
                    "price": price,
                    "stop": m.stop_loss,
                    "take_profit": m.take_profit,
                    "pnl_pct": round(pnl_pct, 2),
                    "rsi": rsi,
                    "chg5": chg5,
                },
            )
            return {
                "update": {
                    "symbol": m.symbol,
                    "action": "reformulate",
                    "recommendation": rec,
                    "stop": m.stop_loss,
                    "take_profit": m.take_profit,
                    "price": price,
                    "pnl_pct": round(pnl_pct, 2),
                }
            }

        return {
            "update": {
                "symbol": mandate.symbol,
                "action": "hold",
                "recommendation": rec,
                "price": price,
                "pnl_pct": round(pnl_pct, 2),
            }
        }

    async def _technical_snapshot(self, symbol: str) -> dict[str, Any]:
        quote = await self._market.get_quote(symbol)
        price = float(quote.get("current_price") or 0)
        df = await self._market.get_history(symbol, period="3mo", interval="1d")
        if df is None or getattr(df, "empty", True) or price <= 0:
            return {
                "price": price,
                "levels": {
                    "stop_loss": round(price * 0.95, 4) if price else None,
                    "take_profit_1": round(price * 1.06, 4) if price else None,
                },
                "bias": "hold",
                "thesis": "Sin histórico suficiente; se mantiene mandato.",
                "rsi": None,
                "chg5": None,
            }

        work = df.copy()
        if not isinstance(work.index, pd.DatetimeIndex):
            work.index = pd.to_datetime(work.index)
        work = enrich_indicators(work)
        sr = detect_support_resistance(work)
        atr_series = work["ATR"] if "ATR" in work.columns else None
        atr = float(atr_series.dropna().iloc[-1]) if atr_series is not None and not atr_series.dropna().empty else price * 0.02
        levels = build_trade_levels(price, sr["support"], sr["resistance"], atr)

        rsi = None
        if "RSI" in work.columns and not work["RSI"].dropna().empty:
            rsi = float(work["RSI"].dropna().iloc[-1])
        chg5 = None
        closes = work["Close"].dropna()
        if len(closes) >= 6 and float(closes.iloc[-6]) > 0:
            chg5 = float((closes.iloc[-1] / closes.iloc[-6] - 1.0) * 100)

        # Simple bias for reformulation without full committee
        bias = "hold"
        if rsi is not None and chg5 is not None:
            if rsi < 35 and chg5 > -2:
                bias = "buy"
            elif rsi > 70 and chg5 < 0:
                bias = "sell"
            elif chg5 >= 3 and (rsi is None or rsi < 68):
                bias = "buy"
            elif chg5 <= -5:
                bias = "sell"

        thesis = (
            f"Revisión técnica continua: RSI={rsi and round(rsi,1)}, "
            f"5d={chg5 and round(chg5,1)}%, ATR={round(atr,4)}, "
            f"soporte={round(sr['support'],4)}, resistencia={round(sr['resistance'],4)}"
        )
        return {
            "price": price,
            "levels": levels,
            "bias": bias,
            "thesis": thesis,
            "rsi": rsi,
            "chg5": chg5,
        }
