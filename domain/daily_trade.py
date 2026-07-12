"""Daily short-term trade recommendation models."""

from datetime import date, datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TradePick(BaseModel):
    ticker: str
    company_name: str | None = None
    action: str = "compra"
    horizon: str = "1-5 días"
    score: float = 0.0
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    current_price: float | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    expected_return_pct: float | None = None
    change_1d_pct: float | None = None
    change_5d_pct: float | None = None
    volume_spike: float | None = None
    rsi: float | None = None
    social_buzz_score: float = 0.0
    catalysts: list[str] = Field(default_factory=list)
    rationale: str = ""
    risks: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class DailyTradeReport(BaseModel):
    report_date: date
    generated_at: datetime = Field(default_factory=utc_now)
    session: str = "pre_market"
    market_regime: str | None = None
    summary: str = ""
    picks: list[TradePick] = Field(default_factory=list)
    disclaimer: str = (
        "Recomendaciones orientativas de corto plazo basadas en tendencias y momentum. "
        "No constituyen asesoría financiera. Opera con stop-loss y gestión de riesgo."
    )
