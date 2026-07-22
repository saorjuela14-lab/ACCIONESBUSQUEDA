"""Paper-first auto-execute desk with LIVE promotion gates."""

from __future__ import annotations

from config.settings import get_settings
from domain.broker import ExecuteLine, ExecuteOrdersRequest
from domain.ops import AutoExecutePolicy
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
        return AutoExecutePolicy(
            enabled=s.auto_execute_trades,
            paper_only_until_promoted=s.auto_execute_paper_first,
            live_enabled=s.auto_execute_live,
            max_notional=s.auto_execute_max_notional,
            require_market_open=s.auto_execute_require_market_open,
        )

    def can_auto_trade(self) -> tuple[bool, str]:
        s = self._settings
        if not s.auto_execute_trades:
            return False, "AUTO_EXECUTE_TRADES=false"
        if not self._broker.is_configured():
            return False, "Alpaca no configurada"
        if self._broker.paper:
            return True, "paper mode OK"
        # LIVE path — require env + optional durable promotion flag
        if s.auto_execute_paper_first and not s.auto_execute_live:
            return False, (
                "LIVE bloqueado: primero opera en paper "
                "(ALPACA_PAPER=true) o define AUTO_EXECUTE_LIVE=true"
            )
        if not s.auto_execute_live:
            return False, "AUTO_EXECUTE_LIVE=false"
        return True, "live promoted"

    async def can_auto_trade_async(self) -> tuple[bool, str]:
        ok, reason = self.can_auto_trade()
        if not ok:
            return ok, reason
        if await KillSwitchService(self._session, self._broker).is_active():
            return False, "kill_switch_active"
        # Durable paper→LIVE promotion gate
        if not self._broker.paper:
            from database.repositories.ops_repository import OpsFlagRepository

            promo = await OpsFlagRepository(self._session).get_json("paper_promotion")
            if self._settings.auto_execute_paper_first and not promo.get("promoted"):
                if not self._settings.auto_execute_live:
                    return False, "paper_promotion_required"
                # AUTO_EXECUTE_LIVE=true can override missing flag, but warn via reason
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

        max_n = float(self._settings.auto_execute_max_notional)
        lines: list[ExecuteLine] = []
        for pick in picks[:3]:
            action = getattr(pick, "action", "") or ""
            if action == "vigilar":
                continue
            ticker = getattr(pick, "ticker", None)
            price = getattr(pick, "current_price", None) or getattr(pick, "entry_price", None)
            if not ticker or not price or price <= 0:
                continue
            shares = int(max_n // float(price))
            if shares < 1:
                continue
            lines.append(
                ExecuteLine(
                    ticker=str(ticker).upper(),
                    shares=float(shares),
                    side="buy",
                    order_type="market",
                    stop_loss=getattr(pick, "stop_loss", None),
                    take_profit=getattr(pick, "target_price", None),
                )
            )
        if not lines:
            return {"skipped": True, "reason": "no_affordable_lines"}

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
