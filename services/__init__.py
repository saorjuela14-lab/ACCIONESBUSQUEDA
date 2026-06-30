"""Application services."""

__all__ = [
    "AnalysisService",
    "PortfolioService",
    "SchedulerService",
    "WatchlistService",
]


def __getattr__(name: str):
    import importlib

    services = {
        "AnalysisService": "services.analysis_service",
        "PortfolioService": "services.portfolio_service",
        "SchedulerService": "services.scheduler_service",
        "WatchlistService": "services.watchlist_service",
    }
    if name in services:
        module = importlib.import_module(services[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
