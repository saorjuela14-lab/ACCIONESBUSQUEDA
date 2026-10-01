"""Paper-first / firm-autonomy auto-execute desk with LIVE promotion gates."""

from __future__ import annotations

import time

from config.settings import get_settings
from domain.broker import ExecuteLine, ExecuteOrdersRequest
from domain.ops import AutoExecutePolicy
from services.committee_consensus import is_actionable_source
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from services.kill_switch_service import KillSwitchService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)


class AutoExecuteService:
    def __init__(
        self,
        session: AsyncSession,
        broker: AlpacaOrderService | None = None,
    ) -> None:
        self._session = session
        self._broker = broker or AlpacaOrderService()
        self._settings = get_settings()
        self._audit = AuditService(session)

    def policy(self) -> AutoExecutePolicy:
        s = self._settings
        enabled = bool(s.auto_execute_trades or s.firm_autonomy)
        live = bool(s.auto_execute_live or s.firm_autonomy)
        return AutoExecutePolicy(
            enabled=enabled,
            paper_only_until_promoted=bool(s.auto_execute_paper_first and not s.firm_autonomy),
            live_enabled=live,
            max_notional=s.auto_execute_max_notional,
            require_market_open=s.auto_execute_require_market_open,
            promotion_note=(
                "Firma autónoma ON: compras/cierres sin click humano "
                f"(tope ${s.auto_execute_max_notional:.0f}/orden, comité + risk desk). "
                f"Estado: FIRM_AUTONOMY={s.firm_autonomy} AUTO_EXECUTE_TRADES={s.auto_execute_trades} "
                f"AUTO_EXECUTE_LIVE={s.auto_execute_live}"
                if s.firm_autonomy
                else (
                    "Paper primero: AUTO_EXECUTE_TRADES=true con ALPACA_PAPER=true. "
                    "LIVE solo con AUTO_EXECUTE_LIVE=true y límites bajos. "
                    f"Estado: AUTO_EXECUTE_TRADES={s.auto_execute_trades} "
                    f"(allowed={enabled})"
                )
            ),
        )

    def _trades_enabled(self) -> bool:
        s = self._settings
        return bool(s.auto_execute_trades or s.firm_autonomy)

    def _live_enabled(self) -> bool:
        s = self._settings
        return bool(s.auto_execute_live or s.firm_autonomy)

    def can_auto_trade(self) -> tuple[bool, str]:
        s = self._settings
        if not self._trades_enabled():
            return False, "AUTO_EXECUTE_TRADES=false"
        if not self._broker.is_configured():
            return False, "Alpaca no configurada"
        if self._broker.paper:
            return True, "paper mode OK"
        # LIVE path
        if s.firm_autonomy:
            return True, "firm_autonomy LIVE"
        if s.auto_execute_paper_first and not self._live_enabled():
            return False, (
                "LIVE bloqueado: primero opera en paper "
                "(ALPACA_PAPER=true) o define AUTO_EXECUTE_LIVE=true"
            )
        if not self._live_enabled():
            return False, "AUTO_EXECUTE_LIVE=false"
        return True, "live promoted"

    async def can_auto_trade_async(self) -> tuple[bool, str]:
        ok, reason = self.can_auto_trade()
        if not ok:
            return ok, reason
        if await KillSwitchService(self._session, self._broker).is_active():
            return False, "kill_switch_active"
        if self._broker.paper or self._settings.firm_autonomy:
            return True, reason
        # Durable paper→LIVE promotion gate (legacy path)
        from database.repositories.ops_repository import OpsFlagRepository

        promo = await OpsFlagRepository(self._session).get_json("paper_promotion")
        if self._settings.auto_execute_paper_first and not promo.get("promoted"):
            if not self._live_enabled():
                return False, "paper_promotion_required"
            return True, "live via AUTO_EXECUTE_LIVE (promotion flag ausente)"
        return True, reason

    async def run_from_picks(self, picks: list, *, actor: str = "scheduler") -> dict:
        ok, reason = await self.can_auto_trade_async()
        if not ok:
            logger.info("auto_execute.skip", reason=reason)
            return {"skipped": True, "reason": reason}

        from services.live_safety import (
            FLAG_ENTRY_DAY,
            FLAG_SUBMIT_FAILS,
            entry_day_allowed,
            live_buys_allowed,
            submit_already_paused,
        )

        entries_ok, entries_why = live_buys_allowed(
            paper=bool(self._broker.paper),
            live_entries_enabled=bool(getattr(self._settings, "live_entries_enabled", False)),
        )
        if not entries_ok:
            return {"skipped": True, "reason": entries_why}

        flags = None
        try:
            from database.repositories.ops_repository import OpsFlagRepository

            flags = OpsFlagRepository(self._session)
            fail_flag = await flags.get_json(FLAG_SUBMIT_FAILS)
            if submit_already_paused(fail_flag):
                return {"skipped": True, "reason": "submit_fail_pause"}
            day_flag = await flags.get_json(FLAG_ENTRY_DAY)
            day_ok, day_why, day_flag = entry_day_allowed(
                day_flag,
                max_entries=int(getattr(self._settings, "live_max_entries_per_day", 1) or 1),
            )
            if not day_ok:
                return {"skipped": True, "reason": day_why}
        except Exception as exc:
            logger.warning("auto_execute.entry_budget_failed", error=str(exc))

        # Risk desk OK
        if self.policy().require_risk_desk_ok:
            try:
                from services.risk_policy_service import RiskPolicyService

                status = await RiskPolicyService().status()
                if not status.macro.trading_allowed or status.macro.mode == "crisis":
                    return {
                        "skipped": True,
                        "reason": status.macro.block_reason or "risk_desk_crisis",
                    }
            except Exception as exc:
                logger.warning("auto_execute.risk_check_failed", error=str(exc))

        if self._settings.auto_execute_require_market_open:
            try:
                clock = await self._broker.get_clock()
                if not clock.is_open:
                    return {"skipped": True, "reason": "market_closed"}
            except Exception as exc:
                return {"skipped": True, "reason": f"clock_failed:{exc}"}

        # Intraday-only: do not open new risk inside the EOD flatten window
        if self._settings.intraday_only_enabled:
            from utils.market_hours import in_eod_flat_window

            if in_eod_flat_window(float(self._settings.intraday_flat_minutes_before_close)):
                return {
                    "skipped": True,
                    "reason": "eod_flat_window_no_new_buys",
                }

        # Post-stop cooldown — no immediate rebuy after a protective exit
        try:
            from database.repositories.ops_repository import OpsFlagRepository

            cool = await OpsFlagRepository(self._session).get_json("post_stop_cooldown")
            until = float(cool.get("until") or 0)
            now_ts = time.time()
            if until and now_ts < until:
                remaining = int((until - now_ts) / 60) + 1
                return {
                    "skipped": True,
                    "reason": (
                        f"post_stop_cooldown_{remaining}m"
                        f"(last={cool.get('symbol')})"
                    ),
                }
        except Exception as exc:
            logger.warning("auto_execute.cooldown_check_failed", error=str(exc))

        max_n = float(self._settings.auto_execute_max_notional)
        cash = 0.0
        equity = 0.0
        try:
            account = await self._broker.get_account()
            cash = float(account.cash or 0)
            equity = float(account.equity or cash or 0)
        except Exception as exc:
            logger.warning("auto_execute.account_failed", error=str(exc))

        from services.deposited_capital_service import resolve_trading_base

        base_snap = await resolve_trading_base(equity=equity if equity > 0 else None)
        capital_base = base_snap.amount if base_snap.amount and base_snap.amount > 0 else None
        if capital_base is None:
            return {"skipped": True, "reason": "no_trading_base"}

        # Concentration + Turtle-style risk budget at the stop — % unchanged, $ vs deposited
        pos_pct = float(self._settings.auto_execute_max_position_pct or 0.30)
        risk_pct = float(self._settings.auto_execute_max_risk_pct or 2.5)
        if capital_base > 0 and capital_base <= 50:
            risk_pct = float(self._settings.auto_execute_micro_max_risk_pct or risk_pct)
        book_cap = max_n
        if cash > 0:
            book_cap = min(book_cap, cash * 0.80)
        book_cap = min(book_cap, capital_base * pos_pct)
        risk_budget = capital_base * (risk_pct / 100.0)
        if book_cap < 1:
            return {"skipped": True, "reason": "insufficient_buying_power"}

        # Ultra-micro: one open line so a recovering name is not starved by AMC recycling
        open_syms: set[str] = set()
        try:
            positions = await self._broker.get_positions()
            open_syms = {
                (p.symbol or "").upper()
                for p in positions
                if (p.symbol or "").upper() not in {"USDTUSD", "USDCUSD"}
                and float(getattr(p, "qty", 0) or 0) > 0
            }
        except Exception as exc:
            logger.warning("auto_execute.positions_failed", error=str(exc))
        micro = equity > 0 and equity <= float(getattr(self._settings, "lifecycle_micro_equity_usd", 50) or 50)
        max_open = int(getattr(self._settings, "auto_execute_micro_max_open", 1) or 1)
        if micro and max_open > 0 and len(open_syms) >= max_open:
            return {
                "skipped": True,
                "reason": f"micro_max_open_{max_open}_hold={','.join(sorted(open_syms)[:4])}",
            }

        avoid: set[str] = set()
        try:
            from services.desk_learning_service import DeskLearningService

            avoid = {t.upper() for t in await DeskLearningService(self._session).avoid_tickers()}
        except Exception as exc:
            logger.warning("auto_execute.avoid_failed", error=str(exc))

        lines: list[ExecuteLine] = []
        skipped_no_committee = 0
        skipped_risk = 0
        skipped_avoid = 0
        for pick in picks[:5]:
            action = getattr(pick, "action", "") or ""
            if action == "vigilar":
                continue
            # Firm rule: committee tag required (unanimous or micro majority)
            unanimous = bool(getattr(pick, "committee_unanimous", False))
            sources = getattr(pick, "sources", None) or []
            if not unanimous and not is_actionable_source(sources):
                skipped_no_committee += 1
                continue
            ticker = getattr(pick, "ticker", None)
            price = getattr(pick, "current_price", None) or getattr(pick, "entry_price", None)
            if not ticker or not price or price <= 0:
                continue
            if str(ticker).upper() in avoid or str(ticker).upper() in open_syms:
                skipped_avoid += 1
                continue
            price_f = float(price)
            stop = getattr(pick, "stop_loss", None)
            if stop is None or float(stop) <= 0 or float(stop) >= price_f:
                stop = round(price_f * (1 - float(self._settings.lifecycle_micro_default_stop_pct or 0.08)), 4)
            else:
                stop = float(stop)
            tp = getattr(pick, "target_price", None)
            if tp is None or float(tp) <= price_f:
                tp = round(price_f * (1 + float(self._settings.lifecycle_micro_default_target_pct or 0.16)), 4)
            else:
                tp = float(tp)

            risk_per_share = max(price_f - float(stop), price_f * 0.01)
            max_by_risk = int(risk_budget // risk_per_share) if risk_per_share > 0 else 0
            max_by_notional = int(book_cap // price_f)
            shares = min(max_by_notional, max_by_risk) if max_by_risk > 0 else 0
            # Allow 1-lot micro ticket only if that single share's stop risk fits the budget
            if shares < 1 and max_by_notional >= 1 and risk_per_share <= risk_budget + 0.01:
                shares = 1
            if shares < 1:
                skipped_risk += 1
                continue
            lines.append(
                ExecuteLine(
                    ticker=str(ticker).upper(),
                    shares=float(shares),
                    side="buy",
                    order_type="market",
                    stop_loss=stop,
                    take_profit=tp,
                )
            )
            if len(lines) >= 2:
                break
        if not lines:
            if skipped_avoid and not skipped_no_committee and not skipped_risk:
                reason_out = "avoid_or_already_open"
            elif skipped_no_committee:
                reason_out = "no_committee_consensus"
            elif skipped_risk:
                reason_out = "risk_budget_blocks_size"
            else:
                reason_out = "no_affordable_lines"
            return {"skipped": True, "reason": reason_out}

        result = await self._broker.execute(
            ExecuteOrdersRequest(
                lines=lines,
                dry_run=False,
                confirm_live=not self._broker.paper,
            )
        )
        try:
            from database.repositories.ops_repository import OpsFlagRepository
            from services.live_safety import (
                FLAG_ENTRY_DAY,
                FLAG_SUBMIT_FAILS,
                entry_day_allowed,
                record_entry_day_fill,
                submit_fail_pause,
            )

            flags = OpsFlagRepository(self._session)
            if result.submitted:
                day_flag = await flags.get_json(FLAG_ENTRY_DAY)
                _, _, day_flag = entry_day_allowed(
                    day_flag,
                    max_entries=int(getattr(self._settings, "live_max_entries_per_day", 1) or 1),
                )
                for od in result.submitted:
                    day_flag = record_entry_day_fill(day_flag, od.symbol)
                    break  # max 1 entry/day — count the batch as one
                await flags.set_json(FLAG_ENTRY_DAY, day_flag)
            fail_flag = await flags.get_json(FLAG_SUBMIT_FAILS)
            paused, pause_why, fail_flag = submit_fail_pause(
                fail_flag,
                submitted=len(result.submitted),
                failed=len(result.failed),
                max_fails=int(getattr(self._settings, "live_submit_fail_pause", 3) or 3),
            )
            await flags.set_json(FLAG_SUBMIT_FAILS, fail_flag)
            if paused:
                logger.warning("auto_execute.submit_fail_pause", reason=pause_why)
        except Exception as exc:
            logger.warning("auto_execute.entry_budget_persist_failed", error=str(exc))
        await self._audit.record(
            "auto_execute",
            actor=actor,
            paper=result.paper,
            success=len(result.failed) == 0,
            message=(
                f"submitted={len(result.submitted)} failed={len(result.failed)} "
                f"({reason})"
            ),
            payload={
                "symbols": [ln.ticker for ln in lines],
                "warnings": result.warnings[:5],
                "firm_autonomy": self._settings.firm_autonomy,
            },
        )
        return {
            "skipped": False,
            "paper": result.paper,
            "submitted": len(result.submitted),
            "failed": len(result.failed),
            "warnings": result.warnings,
            "mode_reason": reason,
        }
