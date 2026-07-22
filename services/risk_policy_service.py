"""Hard risk gates for recommendations and live order submission."""

from __future__ import annotations

from typing import Any, Sequence

from config.settings import get_settings
from domain.risk import (
    OrderRiskVerdict,
    PortfolioRiskSnapshot,
    PositionRiskView,
    RiskDeskStatus,
    RiskPolicy,
)
from services.macro_regime_service import MacroRegimeService
from utils.logging import get_logger

logger = get_logger(__name__)


class RiskPolicyService:
    """Applies firm risk policy + macro regime to sizing and buy/sell gates."""

    def __init__(self, macro_service: MacroRegimeService | None = None) -> None:
        self._macro = macro_service or MacroRegimeService()

    def policy_from_settings(self) -> RiskPolicy:
        s = get_settings()
        return RiskPolicy(
            max_position_pct=s.risk_max_position_pct,
            max_sector_pct=s.risk_max_sector_pct,
            max_gross_exposure_pct=s.risk_max_gross_exposure_pct,
            cash_reserve_pct=s.risk_cash_reserve_pct,
            max_daily_loss_pct=s.risk_max_daily_loss_pct,
            max_open_positions=s.risk_max_open_positions,
            require_stop_loss=s.risk_require_stop_loss,
            min_reward_risk=s.risk_min_reward_risk,
            risk_off_size_mult=s.risk_off_size_mult,
            crisis_block_buys=s.risk_crisis_block_buys,
            auto_execute=s.auto_execute_trades,
            auto_execute_max_notional=s.auto_execute_max_notional,
        )

    def portfolio_from_broker(
        self,
        *,
        equity: float,
        cash: float,
        buying_power: float = 0.0,
        positions: Sequence[Any] | None = None,
        day_pl_pct: float | None = None,
    ) -> PortfolioRiskSnapshot:
        pos_views: list[PositionRiskView] = []
        invested = 0.0
        for p in positions or []:
            if hasattr(p, "model_dump"):
                data = p.model_dump()
            elif isinstance(p, dict):
                data = p
            else:
                continue
            mv = float(data.get("market_value") or 0)
            invested += max(mv, 0)
            weight = (mv / equity * 100) if equity > 0 else 0.0
            plpc = data.get("unrealized_plpc")
            try:
                plpc_f = float(plpc) * (100.0 if abs(float(plpc)) <= 1.5 else 1.0) if plpc is not None else None
            except (TypeError, ValueError):
                plpc_f = None
            pos_views.append(
                PositionRiskView(
                    symbol=str(data.get("symbol") or data.get("ticker") or "").upper(),
                    market_value=mv,
                    weight_pct=round(weight, 2),
                    sector=data.get("sector"),
                    unrealized_pl_pct=round(plpc_f, 2) if plpc_f is not None else None,
                )
            )
        pos_views.sort(key=lambda x: x.market_value, reverse=True)
        cash_pct = (cash / equity * 100) if equity > 0 else 100.0
        invested_pct = (invested / equity * 100) if equity > 0 else 0.0
        top = pos_views[0].weight_pct if pos_views else 0.0
        return PortfolioRiskSnapshot(
            equity=equity,
            cash=cash,
            buying_power=buying_power,
            invested_pct=round(invested_pct, 2),
            cash_pct=round(cash_pct, 2),
            open_positions=len(pos_views),
            positions=pos_views,
            day_pl_pct=day_pl_pct,
            concentration_top_pct=round(top, 2),
        )

    async def status(
        self,
        *,
        market_regime: str | None = None,
        portfolio: PortfolioRiskSnapshot | None = None,
    ) -> RiskDeskStatus:
        policy = self.policy_from_settings()
        macro = await self._macro.assess(market_regime=market_regime)
        notes: list[str] = [macro.thesis]
        if not macro.trading_allowed:
            notes.append(macro.block_reason or "Compras bloqueadas por régimen.")
        if policy.auto_execute:
            notes.append(
                f"AUTO_EXECUTE activo (tope ${policy.auto_execute_max_notional:.0f}/orden). "
                "Úsalo solo con paper o capital que puedas perder."
            )
        else:
            notes.append("Auto-execute OFF — el humano confirma cada orden LIVE.")
        return RiskDeskStatus(
            policy=policy,
            macro=macro,
            portfolio=portfolio,
            auto_execute_enabled=policy.auto_execute,
            notes=notes,
        )

    def evaluate_buy(
        self,
        *,
        symbol: str,
        qty: float,
        price: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        policy: RiskPolicy,
        macro_mode: str,
        size_multiplier: float,
        portfolio: PortfolioRiskSnapshot | None,
        trading_allowed: bool = True,
        block_reason: str | None = None,
    ) -> OrderRiskVerdict:
        """Hard gate for a single buy line. May reduce qty; never increases."""
        reasons: list[str] = []
        warnings: list[str] = []
        sym = symbol.upper().strip()
        adj_qty = float(qty)
        mult = max(0.0, min(float(size_multiplier), 1.5))

        if not trading_allowed or (policy.crisis_block_buys and macro_mode == "crisis"):
            return OrderRiskVerdict(
                allowed=False,
                adjusted_qty=0,
                size_multiplier=0,
                reasons=[block_reason or "Régimen crisis: compras bloqueadas."],
                macro_mode=macro_mode,  # type: ignore[arg-type]
            )

        if macro_mode == "risk_off":
            mult = min(mult, policy.risk_off_size_mult)
            warnings.append(f"Risk-off: tamaño ×{mult:.2f}.")

        if portfolio and portfolio.day_pl_pct is not None:
            if portfolio.day_pl_pct <= -abs(policy.max_daily_loss_pct):
                return OrderRiskVerdict(
                    allowed=False,
                    adjusted_qty=0,
                    size_multiplier=0,
                    reasons=[
                        f"Pérdida diaria {portfolio.day_pl_pct:.1f}% ≥ límite "
                        f"-{policy.max_daily_loss_pct:.1f}% — kill switch."
                    ],
                    macro_mode=macro_mode,  # type: ignore[arg-type]
                )

        if portfolio and portfolio.open_positions >= policy.max_open_positions:
            held = {p.symbol for p in portfolio.positions}
            if sym not in held:
                return OrderRiskVerdict(
                    allowed=False,
                    adjusted_qty=0,
                    reasons=[
                        f"Máximo de posiciones abiertas ({policy.max_open_positions}) alcanzado."
                    ],
                    macro_mode=macro_mode,  # type: ignore[arg-type]
                )

        px = float(price) if price and price > 0 else None
        notional = (adj_qty * px) if px else None

        if portfolio and portfolio.equity > 0 and notional is not None:
            # Cash reserve
            max_spend = max(
                0.0,
                portfolio.cash - (portfolio.equity * policy.cash_reserve_pct / 100.0),
            )
            if notional > max_spend + 1e-6:
                if px and max_spend > 0:
                    new_qty = max(0.0, (max_spend / px) * mult)
                    # whole shares for micro books
                    if new_qty >= 1:
                        new_qty = float(int(new_qty))
                    if new_qty < 1 and max_spend < px:
                        return OrderRiskVerdict(
                            allowed=False,
                            adjusted_qty=0,
                            reasons=[
                                f"Reserva de cash {policy.cash_reserve_pct:.0f}%: "
                                f"no cabe 1 acción de {sym} sin romper el mínimo de liquidez."
                            ],
                            warnings=warnings,
                            macro_mode=macro_mode,  # type: ignore[arg-type]
                        )
                    if new_qty + 1e-9 < adj_qty:
                        warnings.append(
                            f"Qty {sym} reducida {adj_qty:.0f}→{new_qty:.0f} por reserva de cash."
                        )
                        adj_qty = new_qty
                        notional = adj_qty * px
                else:
                    return OrderRiskVerdict(
                        allowed=False,
                        adjusted_qty=0,
                        reasons=["Cash insuficiente tras reserva mínima."],
                        macro_mode=macro_mode,  # type: ignore[arg-type]
                    )

            # Max position concentration (existing + new)
            existing = next((p.market_value for p in portfolio.positions if p.symbol == sym), 0.0)
            new_weight = ((existing + (notional or 0)) / portfolio.equity) * 100
            if new_weight > policy.max_position_pct + 0.01:
                allowed_mv = portfolio.equity * policy.max_position_pct / 100.0 - existing
                if px and allowed_mv > 0:
                    new_qty = max(0.0, (allowed_mv / px) * mult)
                    if new_qty >= 1:
                        new_qty = float(int(new_qty))
                    if new_qty + 1e-9 < adj_qty:
                        warnings.append(
                            f"Qty {sym} limitada por concentración máx "
                            f"{policy.max_position_pct:.0f}%."
                        )
                        adj_qty = new_qty
                else:
                    return OrderRiskVerdict(
                        allowed=False,
                        adjusted_qty=0,
                        reasons=[
                            f"Concentración {sym} excedería {policy.max_position_pct:.0f}% del equity."
                        ],
                        macro_mode=macro_mode,  # type: ignore[arg-type]
                    )

            # Gross exposure
            projected_invested = portfolio.equity * portfolio.invested_pct / 100.0 + (notional or 0)
            projected_pct = projected_invested / portfolio.equity * 100
            if projected_pct > policy.max_gross_exposure_pct + 0.5:
                return OrderRiskVerdict(
                    allowed=False,
                    adjusted_qty=0,
                    reasons=[
                        f"Exposición bruta proyectada {projected_pct:.0f}% > "
                        f"máximo {policy.max_gross_exposure_pct:.0f}%."
                    ],
                    warnings=warnings,
                    macro_mode=macro_mode,  # type: ignore[arg-type]
                )

        # Apply macro size multiplier (whole shares when qty was integer-like)
        if mult < 0.999 and adj_qty > 0:
            scaled = adj_qty * mult
            if adj_qty >= 1 and abs(adj_qty - round(adj_qty)) < 1e-6:
                scaled = float(max(0, int(scaled)))
                if scaled < 1 and mult > 0 and adj_qty >= 1:
                    # keep 1 share minimum when still affordable — micro books
                    scaled = 1.0
                    warnings.append(f"Risk sizing: se mantiene 1 acción mínima de {sym}.")
                elif scaled < adj_qty:
                    warnings.append(f"Risk sizing: qty {sym} {adj_qty:.0f}→{scaled:.0f} (×{mult:.2f}).")
            else:
                warnings.append(f"Risk sizing: qty {sym} ×{mult:.2f}.")
            adj_qty = scaled

        if adj_qty <= 0:
            return OrderRiskVerdict(
                allowed=False,
                adjusted_qty=0,
                reasons=["Cantidad ajustada a 0 tras filtros de riesgo."],
                warnings=warnings,
                macro_mode=macro_mode,  # type: ignore[arg-type]
            )

        if policy.require_stop_loss and (stop_loss is None or stop_loss <= 0):
            warnings.append("Stop-loss ausente — Risk Desk debería adjuntar bracket protectivo.")
            return OrderRiskVerdict(
                allowed=True,
                adjusted_qty=adj_qty,
                size_multiplier=mult,
                reasons=reasons,
                warnings=warnings,
                require_stop=True,
                macro_mode=macro_mode,  # type: ignore[arg-type]
            )

        return OrderRiskVerdict(
            allowed=True,
            adjusted_qty=adj_qty,
            size_multiplier=mult,
            reasons=reasons,
            warnings=warnings,
            require_stop=policy.require_stop_loss,
            macro_mode=macro_mode,  # type: ignore[arg-type]
        )

    def filter_picks_for_regime(
        self,
        picks: list[Any],
        *,
        size_multiplier: float,
        mode: str,
        min_score_bump: float = 0.0,
    ) -> list[Any]:
        """Downgrade/filter recommendation picks under defensive regimes."""
        if mode == "crisis":
            # Keep as watch-only narrative: empty actionable list
            return []
        out = []
        for p in picks:
            score = float(getattr(p, "score", 0) or 0)
            if mode == "risk_off" and score < 45 + min_score_bump:
                continue
            # annotate risks if model allows
            risks = list(getattr(p, "risks", []) or [])
            if mode == "risk_off":
                risks.append("Régimen risk-off: tamaño reducido / selectividad alta.")
            if hasattr(p, "model_copy"):
                updates: dict[str, Any] = {"risks": risks}
                if size_multiplier < 1 and hasattr(p, "confidence"):
                    updates["confidence"] = round(
                        min(0.95, float(p.confidence) * (0.85 if mode == "risk_off" else 1.0)),
                        2,
                    )
                out.append(p.model_copy(update=updates))
            else:
                out.append(p)
        return out
