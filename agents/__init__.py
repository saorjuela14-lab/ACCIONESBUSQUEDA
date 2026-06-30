"""Multi-agent analysis layer."""

__all__ = [
    "AlertAgent",
    "BaseAgent",
    "CorporateActionsAgent",
    "CountryRiskAgent",
    "CompanyRiskAgent",
    "FundamentalAgent",
    "InvestmentDirector",
    "InvestmentMemoryAgent",
    "MacroAgent",
    "MarketMonitor",
    "NewsAgent",
    "PortfolioAgent",
    "SentimentAgent",
    "TechnicalAgent",
    "ValuationAgent",
    "WatchlistAgent",
]


def __getattr__(name: str):
    import importlib

    agents = {
        "AlertAgent": "agents.alert_agent",
        "BaseAgent": "agents.base",
        "CorporateActionsAgent": "agents.corporate_actions_agent",
        "CountryRiskAgent": "agents.country_risk_agent",
        "CompanyRiskAgent": "agents.company_risk_agent",
        "FundamentalAgent": "agents.fundamental_agent",
        "InvestmentDirector": "agents.investment_director",
        "InvestmentMemoryAgent": "agents.investment_memory",
        "MacroAgent": "agents.macro_agent",
        "MarketMonitor": "agents.market_monitor",
        "NewsAgent": "agents.news_agent",
        "PortfolioAgent": "agents.portfolio_agent",
        "SentimentAgent": "agents.sentiment_agent",
        "TechnicalAgent": "agents.technical_agent",
        "ValuationAgent": "agents.valuation_agent",
        "WatchlistAgent": "agents.watchlist_agent",
    }
    if name in agents:
        module = importlib.import_module(agents[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
