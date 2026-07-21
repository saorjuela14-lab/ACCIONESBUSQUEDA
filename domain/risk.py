"""Risk policy and macro-regime models for capital-desk decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


RiskMode = Literal["risk_on", "neutral", "risk_off", "crisis"]
MacroBias = Literal["dovish", "neutral", "hawkish", "recessionary"]


class RiskPolicy(BaseModel):
    """Firm-level risk mandate applied to recommendations and order submission."""

    max_position_pct: float = Field(default=35.0, description="% máximo del equity por ticker")
    max_sector_pct: float = Field(default=40.0, description="% máximo por sector (cuando se conoce)")
    max_gross_exposure_pct: float = Field(default=90.0, description="% máximo invertido (resto cash)")
    cash_reserve_pct: float = Field(default=10.0, description="% mínimo de cash a preservar")
    max_daily_loss_pct: float = Field(default=5.0, description="% pérdida diaria → bloquea nuevas compras")
    max_open_positions: int = Field(default=8, description="Máximo de posiciones abiertas")
    require_stop_loss: bool = Field(default=True, description="Exige stop en compras (bracket)")
    min_reward_risk: float = Field(default=1.2, description="Mínimo reward/risk en picks")
    risk_off_size_mult: float = Field(default=0.35, description="Multiplicador de tamaño en risk-off")
    crisis_block_buys: bool = Field(default=True, description="Bloquea compras en crisis")
    auto_execute: bool = Field(default=False, description="Auto-enviar órdenes (off por defecto)")
    auto_execute_max_notional: float = Field(
        default=25.0,
        description="Tope $ por auto-execute (seguridad micro-cuenta)",
    )


class MacroIndicatorSnapshot(BaseModel):
    name: str
    value: float | None = None
    date: str | None = None
    signal: str = "neutral"  # bullish | bearish | neutral
    note: str = ""


class MacroRegimeAssessment(BaseModel):
    """Consolidated macro + market regime used for sizing and gating."""

    assessed_at: datetime = Field(default_factory=utc_now)
    mode: RiskMode = "neutral"
    market_regime: str = "neutral"  # bullish | bearish | neutral (price action)
    macro_bias: MacroBias = "neutral"
    score: float = 0.0  # negative = defensive
    size_multiplier: float = 1.0
    cash_target_pct: float = 10.0
    vix: float | None = None
    fed_funds: float | None = None
    yield_curve_10y2y: float | None = None
    cpi_yoy: float | None = None
    unemployment: float | None = None
    indicators: list[MacroIndicatorSnapshot] = Field(default_factory=list)
    thesis: str = ""
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    trading_allowed: bool = True
    block_reason: str | None = None


class PositionRiskView(BaseModel):
    symbol: str
    market_value: float = 0.0
    weight_pct: float = 0.0
    sector: str | None = None
    unrealized_pl_pct: float | None = None


class PortfolioRiskSnapshot(BaseModel):
    equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    invested_pct: float = 0.0
    cash_pct: float = 100.0
    open_positions: int = 0
    positions: list[PositionRiskView] = Field(default_factory=list)
    day_pl_pct: float | None = None
    concentration_top_pct: float = 0.0


class OrderRiskVerdict(BaseModel):
    allowed: bool = True
    adjusted_qty: float | None = None
    size_multiplier: float = 1.0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    require_stop: bool = False
    macro_mode: RiskMode = "neutral"


class RiskDeskStatus(BaseModel):
    """API/dashboard payload for risk + macro desk."""

    policy: RiskPolicy
    macro: MacroRegimeAssessment
    portfolio: PortfolioRiskSnapshot | None = None
    auto_execute_enabled: bool = False
    notes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
