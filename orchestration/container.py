"""Dependency injection container."""

from dependency_injector import containers, providers
from sqlalchemy.ext.asyncio import AsyncSession

from agents.market_monitor import MarketMonitor
from config.settings import Settings, get_settings
from database.engine import init_db
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.watchlist_repository import WatchlistRepository
from providers.macro.yfinance_macro_provider import YFinanceMacroProvider
from providers.market.yfinance_provider import YFinanceProvider
from providers.news.duckduckgo_provider import DuckDuckGoNewsProvider
from services.analysis_service import AnalysisService
from services.portfolio_service import PortfolioService
from services.scheduler_service import SchedulerService
from services.watchlist_service import WatchlistService


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(packages=["apis"])

    config = providers.Singleton(get_settings)

    market_provider = providers.Singleton(YFinanceProvider)
    news_provider = providers.Singleton(DuckDuckGoNewsProvider)
    macro_provider = providers.Singleton(YFinanceMacroProvider)

    session = providers.Dependency(instance_of=AsyncSession)

    watchlist_repo = providers.Factory(WatchlistRepository, session=session)
    portfolio_repo = providers.Factory(PortfolioRepository, session=session)
    memory_repo = providers.Factory(InvestmentMemoryRepository, session=session)
    alert_repo = providers.Factory(AlertRepository, session=session)

    analysis_service = providers.Factory(
        AnalysisService,
        market_provider=market_provider,
        news_provider=news_provider,
        macro_provider=macro_provider,
        alert_repo=alert_repo,
        memory_repo=memory_repo,
        max_concentration_pct=config.provided.max_concentration_pct,
    )

    watchlist_service = providers.Factory(
        WatchlistService,
        repo=watchlist_repo,
        market_provider=market_provider,
    )

    portfolio_service = providers.Factory(
        PortfolioService,
        repo=portfolio_repo,
        market_provider=market_provider,
    )

    market_monitor = providers.Factory(
        MarketMonitor,
        market_provider=market_provider,
        macro_provider=macro_provider,
    )

    scheduler_service = providers.Factory(
        SchedulerService,
        market_monitor=market_monitor,
    )


async def bootstrap() -> Container:
    await init_db()
    return Container()
