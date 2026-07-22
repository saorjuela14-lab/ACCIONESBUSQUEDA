"""Capital-firm autopilot — one cycle: reconcile → risk → holdings → lifecycle → execute."""

from __future__ import annotations

from typing import Any

from config.settings import get_settings
from database.repositories.daily_trade_repository import DailyTradeRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.ops_repository import OpsFlagRepository
from domain.ops import utc_now
from providers.market.factory import get_market_provider
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from services.auto_execute_service import AutoExecuteService
from services.company_discovery_service import CompanyDiscoveryService
from services.daily_trade_recommendation_service import DailyTradeRecommendationService
from services.kill_switch_service import KillSwitchService
from services.position_lifecycle_service import PositionLifecycleService
from services.reconcile_service import ReconcileService
from services.risk_policy_service import RiskPolicyService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)


class AutopilotService:
    """Runs the full capital-desk loop in a single ordered pass."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()
        self._broker = AlpacaOrderService()
        self._audit = AuditService(session)

    async def run(
        self,
        *,
        session_label: str = "autopilot",
        execute_trades: bool | None = None,
        actor: str = "autopilot",
    ) -> dict[str, Any]:
        steps: dict[str, Any] = {"started_at": utc_now().isoformat(), "actor": actor}
        settings = self._settings

        if await KillSwitchService(self._session, self._broker).is_active():
            steps["aborted"] = "kill_switch_active"
            await self._audit.record(
                "auto_execute",
                actor=actor,
                success=False,
                message="Autopilot aborted: kill switch ON",
            )
            return steps

        # 1) Reconcile books
        try:
            recon = await ReconcileService(self._session, self._broker).reconcile(
                sync=settings.reconcile_auto_sync
            )
            steps["reconcile"] = {
                "diffs": len(recon.diffs),
                "synced": recon.synced,
                "portfolio_id": recon.portfolio_id,
                "message": recon.message,
            }
        except Exception as exc:
            steps["reconcile"] = {"error": str(exc)}

        # 2) Risk / macro status
        try:
            risk = await RiskPolicyService().status()
            steps["risk"] = {
                "macro_mode": risk.macro.mode,
                "trading_allowed": risk.macro.trading_allowed,
                "size_multiplier": risk.macro.size_multiplier,
                "thesis": risk.macro.thesis[:200],
            }
            if not risk.macro.trading_allowed:
                steps["buys_blocked"] = risk.macro.block_reason
        except Exception as exc:
            steps["risk"] = {"error": str(exc)}

        # 3) Lifecycle scan (exits first)
        try:
            life = await PositionLifecycleService(self._session, self._broker).scan(
                execute_exits=settings.lifecycle_auto_exit
            )
            steps["lifecycle"] = {
                "positions": life.positions,
                "exits": life.exits,
                "actions": len(life.actions),
                "warnings": life.warnings[:5],
            }
        except Exception as exc:
            steps["lifecycle"] = {"error": str(exc)}

        # 4) Generate / refresh daily picks with capital from Alpaca
        capital = None
        try:
            if self._broker.is_configured():
                account = await self._broker.get_account()
                capital = float(account.equity or account.cash or 0) or None
        except Exception:
            capital = None

        try:
            market = get_market_provider()
            daily = DailyTradeRecommendationService(
                market_provider=market,
                discovery_service=CompanyDiscoveryService(market_provider=market),
                trade_repo=DailyTradeRepository(self._session),
            )
            report = await daily.generate(
                session=session_label,
                persist=True,
                capital=capital,
                max_picks=4 if capital and capital <= 100 else 8,
            )
            steps["recommendations"] = {
                "picks": len(report.picks),
                "macro_mode": report.macro_mode,
                "tickers": [p.ticker for p in report.picks[:6]],
                "summary": (report.summary or "")[:240],
            }
        except Exception as exc:
            steps["recommendations"] = {"error": str(exc)}
            report = None

        # 5) Auto-execute (paper-first policy)
        do_exec = settings.auto_execute_trades if execute_trades is None else execute_trades
        if do_exec and report and report.picks:
            try:
                auto = AutoExecuteService(self._session, self._broker)
                # Honor risk desk OK
                if getattr(auto.policy(), "require_risk_desk_ok", True):
                    mode = (steps.get("risk") or {}).get("macro_mode")
                    if mode == "crisis" or steps.get("buys_blocked"):
                        steps["auto_execute"] = {
                            "skipped": True,
                            "reason": "risk_desk_blocked",
                        }
                    else:
                        steps["auto_execute"] = await auto.run_from_picks(
                            report.picks, actor=actor
                        )
                else:
                    steps["auto_execute"] = await auto.run_from_picks(
                        report.picks, actor=actor
                    )
            except Exception as exc:
                steps["auto_execute"] = {"error": str(exc)}
        else:
            steps["auto_execute"] = {
                "skipped": True,
                "reason": "execute_disabled_or_no_picks",
            }

        # 6) Paper promotion snapshot
        try:
            flags = OpsFlagRepository(self._session)
            promo = await flags.get_json("paper_promotion")
            steps["paper_promotion"] = promo or {
                "promoted": False,
                "hint": "POST /ops/autopilot/promote-live tras paper soak",
            }
        except Exception:
            pass

        await self._audit.record(
            "auto_execute",
            actor=actor,
            paper=self._broker.paper if self._broker.is_configured() else None,
            success=True,
            message="Autopilot cycle complete",
            payload={k: v for k, v in steps.items() if k != "started_at"},
        )
        steps["finished_at"] = utc_now().isoformat()
        logger.info(
            "autopilot.done",
            picks=(steps.get("recommendations") or {}).get("picks"),
            exits=(steps.get("lifecycle") or {}).get("exits"),
            exec_skipped=(steps.get("auto_execute") or {}).get("skipped"),
        )
        return steps
