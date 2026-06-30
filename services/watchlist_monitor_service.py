"""Autonomous watchlist monitoring engine."""

from datetime import datetime, timezone

from agents.news_agent import NewsAgent
from agents.technical_agent import TechnicalAgent
from database.repositories.watchlist_repository import WatchlistRepository
from database.repositories.watchlist_snapshot_repository import WatchlistSnapshotRepository
from domain.entities import Alert
from domain.enums import AlertSeverity, AlertType
from providers.interfaces import MarketDataProvider, NewsProvider
from services.alert_service import AlertService
from utils.logging import get_logger

logger = get_logger(__name__)


class WatchlistMonitorService:
    """Scans watchlist tickers and detects material changes."""

    PRICE_MOVE_PCT = 3.0
    TECH_SCORE_DELTA = 15.0

    def __init__(
        self,
        watchlist_repo: WatchlistRepository,
        snapshot_repo: WatchlistSnapshotRepository,
        alert_service: AlertService,
        market_provider: MarketDataProvider,
        news_provider: NewsProvider,
    ) -> None:
        self._watchlist = watchlist_repo
        self._snapshots = snapshot_repo
        self._alerts = alert_service
        self._market = market_provider
        self._technical = TechnicalAgent(market_provider)
        self._news = NewsAgent(news_provider)

    async def scan_all(self) -> dict:
        items = await self._watchlist.list_active()
        results = {"scanned": 0, "alerts": 0, "changes": []}

        for item in items:
            try:
                changes, alerts = await self._scan_ticker(item.ticker, item.company_name)
                results["scanned"] += 1
                results["alerts"] += len(alerts)
                if changes:
                    results["changes"].append({"ticker": item.ticker, "changes": changes})
            except Exception as exc:
                logger.warning("watchlist.scan.failed", ticker=item.ticker, error=str(exc))

        logger.info("watchlist.scan.complete", **{k: v for k, v in results.items() if k != "changes"})
        return results

    async def _scan_ticker(self, ticker: str, company_name: str | None) -> tuple[list[str], list[Alert]]:
        ticker = ticker.upper()
        quote = await self._market.get_quote(ticker)
        price = float(quote.get("current_price") or 0)

        tech_report = await self._technical.analyze(ticker)
        try:
            news_report = await self._news.analyze(ticker, company_name=company_name or ticker)
        except Exception as exc:
            logger.warning("watchlist.news.failed", ticker=ticker, error=str(exc))
            from domain.reports import AgentReport

            news_report = AgentReport(
                agent_name="news_agent",
                ticker=ticker,
                score=0.0,
                confidence=0.0,
                summary="News unavailable during scan.",
            )

        current = {
            "price": price,
            "technical_score": tech_report.score,
            "news_bearish": len(news_report.risks),
            "news_bullish": len(news_report.opportunities),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

        previous = await self._snapshots.get(ticker)
        changes: list[str] = []
        alerts: list[Alert] = []

        if previous:
            changes, alerts = self._detect_changes(ticker, previous, current, tech_report.score)

        await self._snapshots.save(ticker, current)

        if alerts:
            await self._alerts.emit_batch(alerts)

        return changes, alerts

    def _detect_changes(
        self, ticker: str, prev: dict, curr: dict, tech_score: float
    ) -> tuple[list[str], list[Alert]]:
        changes: list[str] = []
        alerts: list[Alert] = []

        prev_price = prev.get("price", 0)
        curr_price = curr.get("price", 0)
        if prev_price and curr_price:
            move_pct = ((curr_price - prev_price) / prev_price) * 100
            if move_pct >= self.PRICE_MOVE_PCT:
                changes.append(f"Price up {move_pct:.1f}%")
                alerts.append(
                    Alert(
                        ticker=ticker,
                        alert_type=AlertType.BREAKOUT,
                        severity=AlertSeverity.HIGH,
                        title=f"{ticker} price breakout +{move_pct:.1f}%",
                        description=f"Price moved from ${prev_price:.2f} to ${curr_price:.2f}",
                    )
                )
            elif move_pct <= -self.PRICE_MOVE_PCT:
                changes.append(f"Price down {move_pct:.1f}%")
                alerts.append(
                    Alert(
                        ticker=ticker,
                        alert_type=AlertType.BREAKDOWN,
                        severity=AlertSeverity.HIGH,
                        title=f"{ticker} price breakdown {move_pct:.1f}%",
                        description=f"Price moved from ${prev_price:.2f} to ${curr_price:.2f}",
                    )
                )

        prev_tech = prev.get("technical_score", 0)
        tech_delta = tech_score - prev_tech
        if abs(tech_delta) >= self.TECH_SCORE_DELTA:
            direction = "improved" if tech_delta > 0 else "deteriorated"
            changes.append(f"Technical score {direction} ({tech_delta:+.1f})")
            alert_type = AlertType.TREND_CHANGE
            alerts.append(
                Alert(
                    ticker=ticker,
                    alert_type=alert_type,
                    severity=AlertSeverity.MEDIUM,
                    title=f"{ticker} technical trend change",
                    description=f"Technical score {direction}: {prev_tech:.1f} → {tech_score:.1f}",
                )
            )

        prev_bearish = prev.get("news_bearish", 0)
        curr_bearish = curr.get("news_bearish", 0)
        if curr_bearish >= prev_bearish + 2:
            changes.append(f"Bearish news increased ({prev_bearish} → {curr_bearish})")
            alerts.append(
                Alert(
                    ticker=ticker,
                    alert_type=AlertType.REGULATORY_NEWS,
                    severity=AlertSeverity.MEDIUM,
                    title=f"{ticker} elevated negative news flow",
                    description=f"Bearish news items: {prev_bearish} → {curr_bearish}",
                )
            )

        return changes, alerts
