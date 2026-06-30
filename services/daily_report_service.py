"""Daily investment report builder."""

from datetime import datetime, timezone

from agents.market_monitor import MarketMonitor
from database.repositories.alert_repository import AlertRepository
from database.repositories.report_repository import ReportRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.enums import MarketSession
from domain.reports import DailyInvestmentReport, Finding
from providers.interfaces import MacroProvider, MarketDataProvider
from reports.writer import ReportWriter
from services.watchlist_monitor_service import WatchlistMonitorService
from utils.logging import get_logger

logger = get_logger(__name__)


class DailyReportService:
    def __init__(
        self,
        market_monitor: MarketMonitor,
        watchlist_monitor: WatchlistMonitorService,
        report_repo: ReportRepository,
        alert_repo: AlertRepository,
        watchlist_repo: WatchlistRepository,
        market_provider: MarketDataProvider,
    ) -> None:
        self._monitor = market_monitor
        self._watchlist_monitor = watchlist_monitor
        self._reports = report_repo
        self._alerts = alert_repo
        self._watchlist = watchlist_repo
        self._market = market_provider
        self._writer = ReportWriter()

    async def generate_daily_report(self) -> DailyInvestmentReport:
        market_report = await self._monitor.generate_market_report(MarketSession.POST_MARKET)
        scan_result = await self._watchlist_monitor.scan_all()
        watchlist = await self._watchlist.list_active()
        active_alerts = await self._alerts.list_unacknowledged(20)

        performers = await self._rank_watchlist_performance(watchlist)

        report = DailyInvestmentReport(
            date=datetime.now(timezone.utc),
            market_report=market_report,
            top_opportunities=[p["ticker"] for p in performers.get("best", [])[:5]],
            worst_performers=[p["ticker"] for p in performers.get("worst", [])[:5]],
            watchlist_changes=[
                f"{c['ticker']}: {', '.join(c['changes'])}"
                for c in scan_result.get("changes", [])
            ],
            alerts=[a.title for a in active_alerts[:10]],
        )

        await self._reports.save_daily_report(report)
        await self._reports.save_market_report(market_report)
        self._writer.write_daily_report(report)

        logger.info(
            "daily_report.generated",
            opportunities=len(report.top_opportunities),
            alerts=len(report.alerts),
            watchlist_changes=len(report.watchlist_changes),
        )
        return report

    async def _rank_watchlist_performance(self, watchlist: list) -> dict:
        best: list[dict] = []
        worst: list[dict] = []

        for item in watchlist:
            try:
                hist = await self._market.get_history(item.ticker, period="5d", interval="1d")
                if hist.empty or len(hist) < 2:
                    continue
                change = ((hist["Close"].iloc[-1] / hist["Close"].iloc[-2]) - 1) * 100
                entry = {"ticker": item.ticker, "change_pct": change}
                if change > 0:
                    best.append(entry)
                else:
                    worst.append(entry)
            except Exception:
                continue

        best.sort(key=lambda x: x["change_pct"], reverse=True)
        worst.sort(key=lambda x: x["change_pct"])
        return {"best": best, "worst": worst}
