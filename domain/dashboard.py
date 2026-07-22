"""Terminal dashboard domain models."""

from datetime import datetime

from pydantic import BaseModel, Field


class IndexQuote(BaseModel):
    symbol: str
    name: str
    price: float | None = None
    change_pct: float | None = None


class SectorHeatmapItem(BaseModel):
    sector: str
    etf: str
    change_pct: float | None = None
    regime: str = "neutral"


class EconomicEvent(BaseModel):
    title: str
    date: str
    importance: str = "medium"
    category: str = "macro"


class NewsHighlight(BaseModel):
    title: str
    source: str
    url: str | None = None
    summary: str | None = None
    published_at: str | None = None
    thumbnail_url: str | None = None
    sentiment: str = "neutral"
    tickers: list[str] = Field(default_factory=list)


class TickerOpportunity(BaseModel):
    ticker: str
    company_name: str | None = None
    recommendation: str
    confidence: float
    score: float
    reason: str


class WatchlistMatrixRow(BaseModel):
    ticker: str
    company_name: str | None = None
    price: float | None = None
    change_pct: float | None = None
    recommendation: str | None = None
    confidence: float | None = None
    news_score: float | None = None
    technical_score: float | None = None
    sentiment_score: float | None = None
    analyzed_at: datetime | None = None


class PriceChartPoint(BaseModel):
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None


class PriceChartData(BaseModel):
    ticker: str
    period: str
    points: list[PriceChartPoint] = Field(default_factory=list)


class TechnicalChartPoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    ema20: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None


class PriceGap(BaseModel):
    timeframe: str
    date: str
    gap_type: str  # gap_up | gap_down
    gap_top: float
    gap_bottom: float
    gap_size_pct: float
    gap_size_abs: float
    fill_target: float
    filled: bool = False
    filled_date: str | None = None
    note: str = ""


class TechnicalSnapshot(BaseModel):
    price: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    ema20: float | None = None
    atr: float | None = None
    bias: str = "neutral"
    support: float | None = None
    resistance: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    risk_reward: float | None = None


class TechnicalChartData(BaseModel):
    ticker: str
    period: str
    chart_timeframe: str = "1D"
    points: list[TechnicalChartPoint] = Field(default_factory=list)
    snapshot: TechnicalSnapshot | None = None
    trade_levels: dict = Field(default_factory=dict)
    summary: str = ""
    gaps: list[PriceGap] = Field(default_factory=list)
    gaps_by_timeframe: dict[str, list[PriceGap]] = Field(default_factory=dict)
    unfilled_gaps: list[PriceGap] = Field(default_factory=list)
    # Freshness relative to "today" — delisted/stale tickers must not look like live analysis
    as_of: str | None = None
    stale_days: int | None = None
    market_status: str = "unavailable"  # live | stale | delisted | unavailable


class PortfolioHistoryPoint(BaseModel):
    timestamp: datetime
    total_value: float
    return_pct: float = 0.0
    cash: float = 0.0


class PortfolioDashboardSlice(BaseModel):
    portfolio_id: str | None = None
    name: str | None = None
    mode: str = "real"
    initial_capital: float = 0.0
    cash: float = 0.0
    total_value: float = 0.0
    return_pct: float = 0.0
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    diversification_score: float | None = None
    sector_weights: dict[str, float] = Field(default_factory=dict)
    country_weights: dict[str, float] = Field(default_factory=dict)
    industry_weights: dict[str, float] = Field(default_factory=dict)
    currency_exposure: dict[str, float] = Field(default_factory=dict)
    cap_exposure: dict[str, float] = Field(default_factory=dict)
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class TerminalDashboard(BaseModel):
    market_regime: str  # bullish | neutral | bearish
    market_regime_score: float = 0.0
    indices: list[IndexQuote] = Field(default_factory=list)
    sector_heatmap: list[SectorHeatmapItem] = Field(default_factory=list)
    economic_calendar: list[EconomicEvent] = Field(default_factory=list)
    market_sentiment_score: float = 0.0
    market_sentiment_label: str = "neutral"
    news_highlights: list[NewsHighlight] = Field(default_factory=list)
    active_alerts: list[str] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    top_opportunities: list[TickerOpportunity] = Field(default_factory=list)
    top_risks: list[TickerOpportunity] = Field(default_factory=list)
    recently_analyzed: list[str] = Field(default_factory=list)
    portfolio: PortfolioDashboardSlice | None = None
    provider_health: dict = Field(default_factory=dict)
    timestamp: datetime | None = None
