"""API request/response schemas."""

from pydantic import BaseModel, Field

from domain.enums import PortfolioMode, StrategyType
from domain.proposal import InvestmentProposal
from domain.reports import InvestmentThesis


class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    portfolio_id: str | None = None


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    notes: str | None = None


class PortfolioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    strategy: StrategyType = StrategyType.GROWTH
    mode: PortfolioMode = PortfolioMode.REAL
    initial_capital: float = Field(gt=0, description="Capital inicial a gestionar")
    cash: float | None = None


class DemoSimulateRequest(BaseModel):
    proposal_budget: float | None = Field(default=None, gt=0)
    expected_return_pct: float = Field(default=12.0, ge=-50, le=100)
    horizon_months: int = Field(default=6, ge=1, le=36)


class AllocationAdviseRequest(BaseModel):
    capital: float = Field(gt=0, description="Capital total a asignar")
    strategy_style: str = Field(
        default="balanced",
        description="emerging_focused | balanced | defensive",
    )


class PositionAddRequest(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    average_cost: float = Field(gt=0)


class ThesisResponse(BaseModel):
    thesis: InvestmentThesis


class InvestmentProposalRequest(BaseModel):
    budget: float = Field(gt=0, description="Total capital to allocate (e.g. 50)")
    tickers: list[str] | None = Field(default=None, description="Tickers to consider")
    use_watchlist: bool = Field(default=False, description="Use active watchlist if tickers empty")
    instrument_mode: str = Field(default="auto", description="auto | stock | cfd")
    risk_profile: str = Field(default="balanced", description="conservative | balanced | aggressive")
    cfd_margin_pct: float | None = Field(default=None, ge=5, le=50, description="CFD margin % (default by risk profile)")
    use_llm_narrative: bool = Field(default=True, description="Enrich with LLM if OPENAI_API_KEY set")
    prefer_affordable: bool = Field(
        default=True,
        description="Priorizar acciones que quepan en el capital (penny stocks si el presupuesto es bajo)",
    )


class ProposalApplyRequest(BaseModel):
    portfolio_id: str
    proposal: InvestmentProposal


class DiscoveryResearchRequest(BaseModel):
    themes: list[str] | None = Field(
        default=None,
        description="Temas a investigar (ej. biotech, semiconductores IA)",
    )
    max_candidates: int = Field(default=15, ge=1, le=30)
    exclude_tickers: list[str] | None = Field(
        default=None,
        description="Tickers a excluir (ej. watchlist actual)",
    )
    max_price: float | None = Field(
        default=None,
        gt=0,
        description="Filtrar candidatos por precio máximo (p. ej. penny ≤ $5)",
    )


class DiscoveryAnalyzeRequest(DiscoveryResearchRequest):
    analyze_top: int = Field(default=3, ge=1, le=5)
    portfolio_id: str | None = None


class DailyTradeGenerateRequest(BaseModel):
    session: str = Field(default="pre_market", description="pre_market | mid_session | post_market")
    max_picks: int = Field(default=8, ge=1, le=15)
    exclude_tickers: list[str] | None = None
    capital: float | None = Field(
        default=None,
        gt=0,
        description="Si se indica, filtra picks asequibles (penny stocks en capital micro)",
    )


class DiscoverProposalRequest(BaseModel):
    budget: float = Field(gt=0, description="Capital total para la propuesta")
    themes: list[str] | None = None
    max_candidates: int = Field(default=15, ge=1, le=30)
    proposal_top: int = Field(default=4, ge=1, le=6, description="Top N descubiertos para propuesta")
    risk_profile: str = Field(default="balanced")
    instrument_mode: str = Field(default="auto")
    add_to_watchlist: bool = Field(default=True)
    use_llm_narrative: bool = Field(default=True)
    portfolio_id: str | None = None


class VoiceCommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="Texto reconocido por voz")
    portfolio_id: str | None = None


class MicroManageRequest(BaseModel):
    capital: float = Field(gt=0, description="Capital del portafolio a gestionar")
    exclude_tickers: list[str] | None = None
    persist_as_daily: bool = Field(
        default=True,
        description="Guardar también como recomendaciones diarias",
    )


class MicroPlanExecuteRequest(BaseModel):
    lines: list[dict] = Field(min_length=1, description="Líneas del plan micro (ticker, shares, stop/target)")
    dry_run: bool = False
    confirm_live: bool = False
    sync_portfolio_id: str | None = None


class TradePickExecuteRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    shares: int = Field(default=1, ge=1, le=10_000)
    stop_loss: float | None = None
    take_profit: float | None = None
    dry_run: bool = False
    confirm_live: bool = False
