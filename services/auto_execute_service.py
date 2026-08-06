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

        # Concentration + Turtle-style risk budget at the stop
        pos_pct = float(self._settings.auto_execute_max_position_pct or 0.30)
        risk_pct = float(self._settings.auto_execute_max_risk_pct or 2.5)
        if equity > 0 and equity <= 50:
            risk_pct = float(self._settings.auto_execute_micro_max_risk_pct or risk_pct)
        book_cap = max_n
        if cash > 0:
            book_cap = min(book_cap, cash * 0.80)
        if equity > 0:
            book_cap = min(book_cap, equity * pos_pct)
        risk_budget = equity * (risk_pct / 100.0) if equity > 0 else book_cap * 0.05
        if book_cap < 1:
            return {"skipped": True, "reason": "insufficient_buying_power"}

        lines: list[ExecuteLine] = []
        skipped_no_committee = 0
        skipped_risk = 0
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
            if skipped_no_committee:
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
