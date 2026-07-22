"""Risk desk + macro regime API."""

from fastapi import APIRouter

from domain.risk import RiskDeskStatus
from services.alpaca_order_service import AlpacaOrderService
from services.market_dashboard_service import MarketDashboardService
from services.risk_policy_service import RiskPolicyService

router = APIRouter()


@router.get("/risk/status", response_model=RiskDeskStatus)
async def risk_status() -> RiskDeskStatus:
    """Firm risk policy + live macro regime (+ Alpaca book when configured)."""
    risk = RiskPolicyService()
    market_regime = "neutral"
    try:
        dash = MarketDashboardService()
        indices, sectors = await dash._fetch_indices(), await dash._fetch_sector_heatmap()
        market_regime, _ = dash._compute_market_regime(indices, sectors)
    except Exception:
        pass

    portfolio = None
    broker = AlpacaOrderService()
    if broker.is_configured():
        try:
            account = await broker.get_account()
            positions = await broker.get_positions()
            portfolio = risk.portfolio_from_broker(
                equity=account.equity or account.portfolio_value or 0.0,
                cash=account.cash,
                buying_power=account.buying_power,
                positions=positions,
            )
        except Exception:
            portfolio = None

    return await risk.status(market_regime=market_regime, portfolio=portfolio)


@router.get("/risk/macro")
async def risk_macro():
    """Macro regime assessment only (lighter)."""
    from services.macro_regime_service import MacroRegimeService

    dash = MarketDashboardService()
    market_regime = "neutral"
    try:
        indices, sectors = await dash._fetch_indices(), await dash._fetch_sector_heatmap()
        market_regime, _ = dash._compute_market_regime(indices, sectors)
    except Exception:
        pass
    assessment = await MacroRegimeService().assess(market_regime=market_regime)
    return assessment
