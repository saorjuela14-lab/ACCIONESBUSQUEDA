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
            service = DailyTradeRecommendationService(
                market_provider=market,
                discovery_service=discovery,
                trade_repo=DailyTradeRepository(session),
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
                await self._maybe_auto_execute(report)
            break

    async def _maybe_auto_execute(self, report) -> None:
        """Optional closed-loop execution — gated by AUTO_EXECUTE_* + Risk Desk."""
        from domain.broker import ExecuteLine, ExecuteOrdersRequest
        from services.alpaca_order_service import AlpacaOrderService

        settings = self._settings
        broker = AlpacaOrderService()
        if not broker.is_configured():
            logger.info("scheduler.auto_execute.skip", reason="alpaca_not_configured")
            return
        if not broker.paper and not settings.auto_execute_live:
            logger.warning(
                "scheduler.auto_execute.blocked",
                reason="LIVE requires AUTO_EXECUTE_LIVE=true (second safety gate)",
            )
            return

        try:
            clock = await broker.get_clock()
            if settings.auto_execute_require_market_open and not clock.is_open:
                logger.info("scheduler.auto_execute.skip", reason="market_closed")
                return
        except Exception as exc:
            logger.warning("scheduler.auto_execute.clock_failed", error=str(exc))
            return

        lines: list[ExecuteLine] = []
        max_n = float(settings.auto_execute_max_notional)
        for pick in report.picks[:3]:
            if pick.action == "vigilar":
                continue
            price = pick.current_price or pick.entry_price
            if not price or price <= 0:
                continue
            shares = int(max_n // price)
            if shares < 1:
                continue
            lines.append(
                ExecuteLine(
                    ticker=pick.ticker,
                    shares=float(shares),
                    side="buy",
                    order_type="market",
                    stop_loss=pick.stop_loss,
                    take_profit=pick.target_price,
                )
            )
        if not lines:
            logger.info("scheduler.auto_execute.skip", reason="no_affordable_lines")
            return

        req = ExecuteOrdersRequest(
            lines=lines,
            dry_run=False,
            confirm_live=not broker.paper,
        )
        result = await broker.execute(req)
        logger.info(
            "scheduler.auto_execute.done",
            submitted=len(result.submitted),
            failed=len(result.failed),
            warnings=result.warnings[:3],
            paper=result.paper,
        )
        if settings.push_daily_trades:
            push = PushNotificationService()
            if push.any_channel_configured:
                await push.notify_message(
                    "Auto-execute Risk Desk",
                    f"OK={len(result.submitted)} FAIL={len(result.failed)} "
                    f"paper={result.paper}\n" + "; ".join(result.warnings[:4]),
                )

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

    def start(self) -> None:
        for time_str in self._settings.report_schedule:
            session = SESSION_MAP.get(time_str, MarketSession.MID_SESSION)
            hour, minute = time_str.split(":")
            self._scheduler.add_job(
                self._run_market_report,
                CronTrigger(hour=int(hour), minute=int(minute)),
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
                CronTrigger(hour=int(hour), minute=int(minute)),
                args=[session_label],
                id=f"daily_trade_{time_str}",
                replace_existing=True,
            )

        # Daily investment report + memory evaluation at post-market (17:30)
        self._scheduler.add_job(
            self._run_daily_report,
            CronTrigger(hour=17, minute=30),
            id="daily_investment_report",
            replace_existing=True,
        )

        # Watchlist scan every N minutes during market hours
        self._scheduler.add_job(
            self._run_watchlist_scan,
            IntervalTrigger(minutes=self._settings.watchlist_scan_interval_minutes),
            id="watchlist_scan",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info(
            "scheduler.started",
            report_times=self._settings.report_schedule,
            trade_times=self._settings.daily_trade_schedule,
            watchlist_interval=self._settings.watchlist_scan_interval_minutes,
        )

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)


async def start_scheduler() -> SchedulerService:
    await init_db()
    scheduler = SchedulerService()
    scheduler.start()
    return scheduler
