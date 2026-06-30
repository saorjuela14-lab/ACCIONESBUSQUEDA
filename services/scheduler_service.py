"""Cloud scheduler for automated market reports."""

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.market_monitor import MarketMonitor
from config.settings import get_settings
from domain.enums import MarketSession
from utils.logging import get_logger

logger = get_logger(__name__)

SESSION_MAP = {
    "08:30": MarketSession.PRE_MARKET,
    "11:30": MarketSession.MID_SESSION,
    "15:00": MarketSession.POWER_HOUR,
    "17:30": MarketSession.POST_MARKET,
}


class SchedulerService:
    def __init__(self, market_monitor: MarketMonitor) -> None:
        self._monitor = market_monitor
        self._scheduler = AsyncIOScheduler(timezone=ZoneInfo(get_settings().market_timezone))
        self._settings = get_settings()

    async def _run_report(self, session: MarketSession) -> None:
        report = await self._monitor.generate_market_report(session)
        logger.info(
            "scheduler.market_report",
            session=session.value,
            strong_sectors=report.strong_sectors,
            weak_sectors=report.weak_sectors,
        )

    def start(self) -> None:
        for time_str in self._settings.report_schedule:
            session = SESSION_MAP.get(time_str, MarketSession.MID_SESSION)
            hour, minute = time_str.split(":")
            self._scheduler.add_job(
                self._run_report,
                CronTrigger(hour=int(hour), minute=int(minute)),
                args=[session],
                id=f"market_report_{time_str}",
                replace_existing=True,
            )
        self._scheduler.start()
        logger.info("scheduler.started", times=self._settings.report_schedule)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
