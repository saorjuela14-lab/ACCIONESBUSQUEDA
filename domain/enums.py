"""Domain enumerations for the investment committee platform."""

from enum import StrEnum


class EvidenceCategory(StrEnum):
    FACT = "fact"
    INTERPRETATION = "interpretation"
    RISK = "risk"
    PROBABILITY = "probability"
    OPINION = "opinion"
    UNCERTAINTY = "uncertainty"


class InvestmentRecommendation(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class NewsSentiment(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class NewsTopicCategory(StrEnum):
    MERGERS_ACQUISITIONS = "mergers_acquisitions"
    LITIGATION = "litigation"
    REGULATORY = "regulatory"
    PRODUCT_PIPELINE = "product_pipeline"
    EARNINGS = "earnings"
    MANAGEMENT = "management"
    STRATEGIC = "strategic"
    GENERAL = "general"


class ImpactLevel(StrEnum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TimeHorizon(StrEnum):
    INTRADAY = "intraday"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    LONG_TERM = "long_term"


class AlertType(StrEnum):
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    GAP = "gap"
    INSIDER_BUYING = "insider_buying"
    INSIDER_SELLING = "insider_selling"
    EARNINGS = "earnings"
    REGULATORY_NEWS = "regulatory_news"
    SENTIMENT_SHIFT = "sentiment_shift"
    TREND_CHANGE = "trend_change"
    RISK_INCREASE = "risk_increase"
    DOWNGRADE = "downgrade"
    UPGRADE = "upgrade"


class AlertSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MarketSession(StrEnum):
    PRE_MARKET = "pre_market"
    MID_SESSION = "mid_session"
    POWER_HOUR = "power_hour"
    POST_MARKET = "post_market"
    DAILY = "daily"


class ReportType(StrEnum):
    PRE_MARKET = "pre_market"
    MID_SESSION = "mid_session"
    POWER_HOUR = "power_hour"
    POST_MARKET = "post_market"
    DAILY = "daily"
    TICKER_ANALYSIS = "ticker_analysis"


class StrategyType(StrEnum):
    VALUE = "value_investing"
    GROWTH = "growth_investing"
    DIVIDEND = "dividend_investing"
    MOMENTUM = "momentum"
    SWING = "swing_trading"
    BREAKOUT = "breakout_trading"
    MEAN_REVERSION = "mean_reversion"
    SECTOR_ROTATION = "sector_rotation"
    SMART_MONEY = "smart_money"


class PortfolioMode(StrEnum):
    REAL = "real"
    DEMO = "demo"
