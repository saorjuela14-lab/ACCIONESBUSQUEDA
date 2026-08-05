"""Cloud scheduler — market reports, watchlist scans, daily reports, memory evaluation."""

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.market_monitor import MarketMonitor
from config.settings import get_settings
from database.engine import get_session, init_db
from database.repositories.alert_repository import AlertRepository
from database.repositories.daily_trade_repository import DailyTradeRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.report_repository import ReportRepository
from database.repositories.watchlist_repository import WatchlistRepository
from database.repositories.watchlist_snapshot_repository import WatchlistSnapshotRepository
from domain.enums import MarketSession
from providers.macro.factory import get_macro_provider
from providers.market.factory import get_market_provider
from providers.news.factory import get_news_provider
from reports.writer import ReportWriter
from services.alert_service import AlertService
from services.daily_report_service import DailyReportService
from services.daily_trade_recommendation_service import DailyTradeRecommendationService
from services.memory_evaluation_service import MemoryEvaluationService
from services.company_discovery_service import CompanyDiscoveryService
from services.push_notification_service import PushNotificationService
from services.watchlist_monitor_service import WatchlistMonitorService
from utils.logging import get_logger
from utils.market_hours import should_run_automation

logger = get_logger(__name__)

SESSION_MAP = {
    "08:30": MarketSession.PRE_MARKET,
    "11:30": MarketSession.MID_SESSION,
    "15:00": MarketSession.POWER_HOUR,
    "17:30": MarketSession.POST_MARKET,
}


class SchedulerService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo(self._settings.market_timezone))
        self._writer = ReportWriter()

    async def _build_daily_report_service(self, session) -> DailyReportService:
        market = get_market_provider()
        macro = get_macro_provider()
        news = get_news_provider()
        alert_repo = AlertRepository(session)
        return DailyReportService(
            market_monitor=MarketMonitor(market, macro),
            watchlist_monitor=WatchlistMonitorService(
                WatchlistRepository(session),
                WatchlistSnapshotRepository(session),
                AlertService(alert_repo, self._settings.alert_cooldown_hours),
                market,
                news,
            ),
            report_repo=ReportRepository(session),
            alert_repo=alert_repo,
            watchlist_repo=WatchlistRepository(session),
            market_provider=market,
        )

    async def _run_market_report(self, session_type: MarketSession) -> None:
        if not should_run_automation():
            logger.info("scheduler.skipped", job="market_report", reason="outside_automation_hours")
            return

        market = get_market_provider()
        macro = get_macro_provider()
        monitor = MarketMonitor(market, macro)
        report = await monitor.generate_market_report(session_type)

        async for session in get_session():
            repo = ReportRepository(session)
            await repo.save_market_report(report)
            break

        self._writer.write_market_report(report)
        logger.info(
            "scheduler.market_report",
            session=session_type.value,
            strong=report.strong_sectors,
            weak=report.weak_sectors,
        )

    async def _run_watchlist_scan(self) -> None:
        if not should_run_automation():
            return

        async for session in get_session():
            market = get_market_provider()
            news = get_news_provider()
            alert_repo = AlertRepository(session)
            monitor = WatchlistMonitorService(
                WatchlistRepository(session),
                WatchlistSnapshotRepository(session),
                AlertService(alert_repo, self._settings.alert_cooldown_hours),
                market,
                news,
            )
            result = await monitor.scan_all()
            logger.info("scheduler.watchlist_scan", **{k: v for k, v in result.items() if k != "changes"})
            break

    async def _run_daily_trade_recommendations(self, session_label: str) -> None:
        if not should_run_automation():
            logger.info("scheduler.skipped", job="daily_trade", reason="outside_automation_hours")
            return

        async for session in get_session():
            market = get_market_provider()
            discovery = CompanyDiscoveryService(market_provider=market)
            from services.analysis_factory import build_analysis_service

            service = DailyTradeRecommendationService(
                market_provider=market,
                discovery_service=discovery,
                trade_repo=DailyTradeRepository(session),
                analysis_service=build_analysis_service(session),
            )
            report = await service.generate(session=session_label, persist=True)
            logger.info(
                "scheduler.daily_trade",
                session=session_label,
                picks=len(report.picks),
                regime=report.market_regime,
                macro_mode=report.macro_mode,
            )
            if self._settings.push_daily_trades and (report.picks or report.macro_mode == "crisis"):
                push = PushNotificationService()
                if push.any_channel_configured:
                    lines = [
                        f"• {p.ticker} ({p.action}) — {p.rationale[:80]}"
                        for p in report.picks[:6]
                    ]
                    body = (
                        f"Sesión: {session_label}\n"
                        f"Régimen: {report.market_regime} | Macro: {report.macro_mode}\n"
                        f"{(report.macro_thesis or '')[:200]}\n\n"
                        + ("\n".join(lines) if lines else "Sin compras (filtro riesgo/macro).")
                    )
                    await push.notify_message("Recomendaciones corto plazo", body)

            if self._settings.auto_execute_trades and report.picks:
                await self._maybe_auto_execute(report, session)
            break

    async def _maybe_auto_execute(self, report, session) -> None:
        from services.auto_execute_service import AutoExecuteService

        result = await AutoExecuteService(session).run_from_picks(report.picks, actor="scheduler")
        logger.info("scheduler.auto_execute", **{k: v for k, v in result.items() if k != "warnings"})
        if self._settings.push_daily_trades and not result.get("skipped"):
            push = PushNotificationService()
            if push.any_channel_configured:
                await push.notify_message(
                    "Auto-execute",
                    f"paper={result.get('paper')} OK={result.get('submitted')} "
                    f"FAIL={result.get('failed')} · {result.get('mode_reason')}",
                )

    async def _run_lifecycle_scan(self) -> None:
        if not self._settings.lifecycle_enabled:
            return
        if not should_run_automation():
            return
        async for session in get_session():
            from services.position_lifecycle_service import PositionLifecycleService

            report = await PositionLifecycleService(session).scan(
                execute_exits=self._settings.lifecycle_auto_exit
            )
            logger.info(
                "scheduler.lifecycle",
                positions=report.positions,
                exits=report.exits,
                warnings=len(report.warnings),
            )
            if report.exits and self._settings.push_daily_trades:
                push = PushNotificationService()
                if push.any_channel_configured:
                    await push.notify_message(
                        "Lifecycle exits",
                        "Cerradas: " + ", ".join(report.exits),
                    )
            break

    async def _run_reconcile(self) -> None:
        if not should_run_automation():
            return
        async for session in get_session():
            from services.reconcile_service import ReconcileService

            report = await ReconcileService(session).reconcile(
                sync=self._settings.reconcile_auto_sync
            )
            logger.info(
                "scheduler.reconcile",
                diffs=len(report.diffs),
                synced=report.synced,
                portfolio_id=report.portfolio_id,
            )
            # Wake-path catch-up: cloud may sleep through exact cron minute
            if self._settings.whatsapp_briefing_enabled:
                from services.status_briefing_catchup_service import StatusBriefingCatchupService

                await StatusBriefingCatchupService(session).catch_up(via="reconcile_catchup")
            break

    async def _run_intraday_flat(self) -> None:
        if not self._settings.intraday_only_enabled:
            return
        if not should_run_automation():
            return
        async for session in get_session():
            from services.intraday_flat_service import IntradayFlatService

            result = await IntradayFlatService(session).run(
                force=True,
                reason="scheduled_eod_flat",
                actor="scheduler_eod",
            )
            logger.info(
                "scheduler.intraday_flat",
                skipped=result.get("skipped"),
                closed=len(result.get("closed") or []),
                carried=len(result.get("carried") or []),
                reason=result.get("reason"),
                message=result.get("message"),
            )
            if ((result.get("closed") or []) or (result.get("carried") or [])) and self._settings.push_daily_trades:
                push = PushNotificationService()
                if push.any_channel_configured:
                    closed_syms = ", ".join(c.get("symbol", "?") for c in (result.get("closed") or [])) or "—"
                    carry_syms = ", ".join(c.get("symbol", "?") for c in (result.get("carried") or [])) or "—"
                    await push.notify_message(
                        "EOD smart flat",
                        f"Aseguradas: {closed_syms} · Carry rojo overnight: {carry_syms}",
                    )
            break

    async def _run_autopilot(self) -> None:
        if not should_run_automation():
            return
        async for session in get_session():
            from services.autopilot_service import AutopilotService

            result = await AutopilotService(session).run(actor="scheduler_autopilot")
            logger.info(
                "scheduler.autopilot",
                aborted=result.get("aborted"),
                picks=(result.get("recommendations") or {}).get("picks"),
                exits=(result.get("lifecycle") or {}).get("exits"),
            )
            break

    async def _run_daily_report(self) -> None:
        async for session in get_session():
            service = await self._build_daily_report_service(session)
            await service.generate_daily_report()
            break

        async for session in get_session():
            memory_svc = MemoryEvaluationService(
                InvestmentMemoryRepository(session),
                get_market_provider(),
            )
            await memory_svc.evaluate_pending()
            break

    async def _run_status_briefing(self, session_kind: str) -> None:
        """WhatsApp/Telegram portfolio status at market open or close."""
        if not self._settings.whatsapp_briefing_enabled:
            return
        kind = session_kind if session_kind in ("open", "lunch", "close") else "manual"
        if kind == "manual":
            from services.daily_status_briefing_service import DailyStatusBriefingService

            result = await DailyStatusBriefingService().send("manual")
            logger.info("scheduler.status_briefing", session=kind, **{
                k: v for k, v in result.items() if k != "title"
            })
            return

        async for session in get_session():
            from services.status_briefing_catchup_service import StatusBriefingCatchupService

            result = await StatusBriefingCatchupService(session).send_if_needed(
                kind,  # type: ignore[arg-type]
                via="cron",
                force=False,
            )
            logger.info("scheduler.status_briefing", session=kind, result=result)
            break

    async def _run_status_briefing_catchup(self) -> None:
        """Silent recovery only: sends a slot if cron was missed — never re-sends."""
        if not self._settings.whatsapp_briefing_enabled:
            return
        from utils.market_hours import is_trading_day

        if not is_trading_day():
            return
        async for session in get_session():
            from services.status_briefing_catchup_service import StatusBriefingCatchupService

            result = await StatusBriefingCatchupService(session).catch_up(via="interval_catchup")
            delivered = False
            for v in result.values():
                if isinstance(v, dict) and (v.get("whatsapp") or v.get("telegram")):
                    delivered = True
                    break
            if delivered:
                logger.info("scheduler.status_briefing_catchup", **result)
            break

    def start(self) -> None:
        tz = ZoneInfo(self._settings.market_timezone)
        for time_str in self._settings.report_schedule:
            session = SESSION_MAP.get(time_str, MarketSession.MID_SESSION)
            hour, minute = time_str.split(":")
            self._scheduler.add_job(
                self._run_market_report,
                CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
                args=[session],
                id=f"market_report_{time_str}",
                replace_existing=True,
            )

        # Daily short-term trade recommendations at pre-market and mid-session
        trade_session_map = {
            "08:30": "pre_market",
            "11:30": "mid_session",
        }
        for time_str in self._settings.daily_trade_schedule:
            session_label = trade_session_map.get(time_str, "pre_market")
            hour, minute = time_str.split(":")
            self._scheduler.add_job(
                self._run_daily_trade_recommendations,
                CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
                args=[session_label],
                id=f"daily_trade_{time_str}",
                replace_existing=True,
            )

        # Daily investment report + memory evaluation at post-market (17:30)
        self._scheduler.add_job(
            self._run_daily_report,
            CronTrigger(hour=17, minute=30, timezone=tz),
            id="daily_investment_report",
            replace_existing=True,
        )

        # Exactly 3 WhatsApp/Telegram status messages: open, lunch, close (ET)
        briefing_kind = {
            "09:35": "open",
            "09:30": "open",
            "12:30": "lunch",
            "12:00": "lunch",
            "13:00": "lunch",
            "16:05": "close",
            "16:00": "close",
        }
        for time_str in self._settings.whatsapp_briefing_schedule:
            hour, minute = time_str.split(":")
            h = int(hour)
            if time_str in briefing_kind:
                kind = briefing_kind[time_str]
            elif h < 11:
                kind = "open"
            elif h < 15:
                kind = "lunch"
            else:
                kind = "close"
            self._scheduler.add_job(
                self._run_status_briefing,
                CronTrigger(hour=h, minute=int(minute), timezone=tz),
                args=[kind],
                id=f"status_briefing_{time_str}",
                replace_existing=True,
                # Survive short sleeps / deploys around the exact minute
                misfire_grace_time=90 * 60,
                coalesce=True,
            )

        # Silent catch-up (does NOT spam — only fills a missed slot once)
        self._scheduler.add_job(
            self._run_status_briefing_catchup,
            IntervalTrigger(minutes=10),
            id="status_briefing_catchup",
            replace_existing=True,
            misfire_grace_time=9 * 60,
            coalesce=True,
        )

        # Watchlist scan every N minutes during market hours
        self._scheduler.add_job(
            self._run_watchlist_scan,
            IntervalTrigger(minutes=self._settings.watchlist_scan_interval_minutes),
            id="watchlist_scan",
            replace_existing=True,
        )

        # Position lifecycle (trailing / time-stop / thesis exit)
        if self._settings.lifecycle_enabled:
            self._scheduler.add_job(
                self._run_lifecycle_scan,
                IntervalTrigger(minutes=self._settings.lifecycle_scan_interval_minutes),
                id="lifecycle_scan",
                replace_existing=True,
            )

        # Intraday-only EOD flatten (default 15:40 ET) + misfire grace for host sleep
        if self._settings.intraday_only_enabled:
            flat_hhmm = (self._settings.intraday_flat_cron or "15:40").strip()
            try:
                fh, fm = flat_hhmm.split(":")
                self._scheduler.add_job(
                    self._run_intraday_flat,
                    CronTrigger(hour=int(fh), minute=int(fm), timezone=tz),
                    id="intraday_eod_flat",
                    replace_existing=True,
                    misfire_grace_time=45 * 60,
                    coalesce=True,
                )
            except Exception as exc:
                logger.warning("scheduler.intraday_flat_cron_invalid", value=flat_hhmm, error=str(exc))

        # Continuous Alpaca ↔ DB reconcile
        self._scheduler.add_job(
            self._run_reconcile,
            IntervalTrigger(minutes=self._settings.reconcile_interval_minutes),
            id="reconcile_books",
            replace_existing=True,
        )

        # Full firm autopilot (firm autonomy defaults to 30m when unset)
        autopilot_every = self._settings.effective_autopilot_interval_minutes
        if autopilot_every > 0:
            self._scheduler.add_job(
                self._run_autopilot,
                IntervalTrigger(minutes=autopilot_every),
                id="firm_autopilot",
                replace_existing=True,
            )

        self._scheduler.start()
        logger.info(
            "scheduler.started",
            report_times=self._settings.report_schedule,
            trade_times=self._settings.daily_trade_schedule,
            watchlist_interval=self._settings.watchlist_scan_interval_minutes,
            lifecycle_interval=self._settings.lifecycle_scan_interval_minutes,
            reconcile_interval=self._settings.reconcile_interval_minutes,
            autopilot_interval=autopilot_every,
            firm_autonomy=self._settings.firm_autonomy,
            whatsapp_briefing=self._settings.whatsapp_briefing_schedule,
        )

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)


async def start_scheduler() -> SchedulerService:
    await init_db()
    scheduler = SchedulerService()
    scheduler.start()
    return scheduler
