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

# Stablecoin parking pairs that trap USD buying power on Alpaca crypto
_USD_PARKING_SYMBOLS = frozenset({"USDTUSD", "USDT/USD", "USDCUSD", "USDC/USD"})


def is_usd_parking_symbol(symbol: str) -> bool:
    """True for USDT/USDC vs USD crypto pairs used as cash parking."""
    sym = (symbol or "").upper().strip()
    return sym in _USD_PARKING_SYMBOLS or sym.replace("/", "") in {
        "USDTUSD",
        "USDCUSD",
    }


class AutopilotService:
    """Runs the full capital-desk loop in a single ordered pass."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()
        self._broker = AlpacaOrderService()
        self._audit = AuditService(session)

    async def _sweep_usd_parking(self) -> dict[str, Any]:
        """Liquidate USDT/USDC parking → USD cash so the desk can size equity buys."""
        if not self._broker.is_configured():
            return {"skipped": True, "reason": "broker_unconfigured"}
        positions = await self._broker.get_positions()
        targets = [p for p in positions if is_usd_parking_symbol(p.symbol)]
        if not targets:
            return {"closed": [], "message": "no_usd_parking"}
        closed: list[dict[str, Any]] = []
        errors: list[str] = []
        for pos in targets:
            close_sym = (pos.symbol or "").upper().replace("/", "")
            try:
                raw = await self._broker.close_position(close_sym)
                closed.append(
                    {
                        "symbol": close_sym,
                        "qty": pos.qty,
                        "market_value": pos.market_value,
                        "status": (raw or {}).get("status") if isinstance(raw, dict) else "submitted",
                        "order_id": (raw or {}).get("id") if isinstance(raw, dict) else None,
                    }
                )
                logger.info(
                    "autopilot.cash_sweep",
                    symbol=close_sym,
                    qty=pos.qty,
                    market_value=pos.market_value,
                )
            except Exception as exc:
                errors.append(f"{close_sym}: {exc}")
        return {"closed": closed, "errors": errors}

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

        # 1b) USDT/USDC parking → USD cash (buying power for equities)
        try:
            steps["cash_sweep"] = await self._sweep_usd_parking()
        except Exception as exc:
            steps["cash_sweep"] = {"error": str(exc)}

        # 1c) Intraday-only: flatten before close / clear overnight leftovers
        try:
            from services.intraday_flat_service import IntradayFlatService

            steps["intraday_flat"] = await IntradayFlatService(
                self._session, self._broker
            ).run(actor=actor)
        except Exception as exc:
            steps["intraday_flat"] = {"error": str(exc)}

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

        # 2b) Continuous holdings strategy review (reformulate thesis → prefer TP)
        try:
            from services.holdings_strategy_review_service import HoldingsStrategyReviewService

            # Technical-first review (latency-safe). Reformulates TP/stop every loop;
            # harvests near take-profit; invalidates on sell bias.
            holdings = await HoldingsStrategyReviewService(
                self._session,
                self._broker,
                analysis=None,
            ).review(execute_exits=settings.lifecycle_auto_exit)
            steps["holdings_review"] = holdings
        except Exception as exc:
            steps["holdings_review"] = {"error": str(exc)}

        # 3) Lifecycle scan (mechanical exits after reformulation)
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
            from services.analysis_factory import build_analysis_service
            from services.desk_learning_service import DeskLearningService

            daily = DailyTradeRecommendationService(
                market_provider=market,
                discovery_service=CompanyDiscoveryService(market_provider=market),
                trade_repo=DailyTradeRepository(self._session),
                analysis_service=build_analysis_service(self._session),
            )
            exclude = await DeskLearningService(self._session).merge_excludes()
            report = await daily.generate(
                session=session_label,
                persist=True,
                capital=capital,
                max_picks=4 if capital and capital <= 100 else 8,
                exclude_tickers=exclude,
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

        # 5) Auto-execute (firm autonomy / paper-first policy)
        if execute_trades is None:
            do_exec = bool(settings.auto_execute_trades or settings.firm_autonomy)
        else:
            do_exec = execute_trades
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

        # 7) Briefing catch-up (open/close WhatsApp) if cron was missed
        try:
            if settings.whatsapp_briefing_enabled:
                from services.status_briefing_catchup_service import StatusBriefingCatchupService

                steps["status_briefing"] = await StatusBriefingCatchupService(
                    self._session
                ).catch_up(via="autopilot_catchup")
        except Exception as exc:
            steps["status_briefing"] = {"error": str(exc)}

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
