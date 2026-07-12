"""Demo portfolio Monte Carlo projections and scenario simulations."""

from __future__ import annotations

import numpy as np

from domain.entities import Portfolio
from domain.enums import PortfolioMode
from domain.portfolio_demo import PortfolioProjectionReport, ProjectionPoint, ScenarioOutcome
from providers.interfaces import MarketDataProvider


class DemoProjectionService:
    """Forward-looking simulations for demo portfolios."""

    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def _estimate_volatility(self, portfolio: Portfolio) -> tuple[float, float]:
        """Return (annual_return_pct, annual_volatility_pct) from holdings or defaults."""
        if not portfolio.positions:
            return 8.0, 18.0

        tickers = [p.ticker for p in portfolio.positions]
        weights = []
        vols = []
        rets = []
        total = portfolio.total_value or portfolio.initial_capital

        for pos in portfolio.positions:
            w = ((pos.current_price or pos.average_cost) * pos.shares) / max(total, 1)
            weights.append(w)
            try:
                df = await self._market.get_history(pos.ticker, period="1y", interval="1d")
                if df.empty or "Close" not in df.columns or len(df) < 30:
                    vols.append(0.22)
                    rets.append(0.08)
                    continue
                daily = df["Close"].pct_change().dropna()
                vols.append(float(daily.std()) * (252 ** 0.5))
                rets.append(float(daily.mean()) * 252)
            except Exception:
                vols.append(0.22)
                rets.append(0.08)

        w_arr = np.array(weights)
        if w_arr.sum() > 0:
            w_arr = w_arr / w_arr.sum()
        ann_ret = float(np.dot(w_arr, rets)) * 100 if rets else 8.0
        ann_vol = float(np.dot(w_arr, vols)) * 100 if vols else 18.0
        return round(max(-20, min(30, ann_ret)), 1), round(max(8, min(45, ann_vol)), 1)

    async def project(self, portfolio: Portfolio, horizon_months: int = 12) -> PortfolioProjectionReport:
        horizon_months = max(3, min(36, horizon_months))
        current = portfolio.total_value or portfolio.initial_capital
        ann_ret, ann_vol = await self._estimate_volatility(portfolio)

        mu_m = (ann_ret / 100) / 12
        sigma_m = (ann_vol / 100) / (12 ** 0.5)

        n_sims = 400
        paths = np.zeros((n_sims, horizon_months + 1))
        paths[:, 0] = current
        rng = np.random.default_rng(42)
        shocks = rng.normal(mu_m, sigma_m, size=(n_sims, horizon_months))
        for t in range(horizon_months):
            paths[:, t + 1] = paths[:, t] * (1 + shocks[:, t])

        points: list[ProjectionPoint] = []
        for m in range(horizon_months + 1):
            col = paths[:, m]
            points.append(
                ProjectionPoint(
                    month=m,
                    label=f"Mes {m}" if m else "Hoy",
                    pessimistic=round(float(np.percentile(col, 10)), 2),
                    base=round(float(np.percentile(col, 50)), 2),
                    optimistic=round(float(np.percentile(col, 90)), 2),
                )
            )

        final = paths[:, -1]
        scenarios = [
            ScenarioOutcome(
                name="Bajista",
                horizon_months=horizon_months,
                projected_value=round(float(np.percentile(final, 10)), 2),
                return_pct=round((float(np.percentile(final, 10)) - current) / current * 100, 1),
                description=f"Escenario pesimista (percentil 10) tras {horizon_months} meses.",
            ),
            ScenarioOutcome(
                name="Base",
                horizon_months=horizon_months,
                projected_value=round(float(np.percentile(final, 50)), 2),
                return_pct=round((float(np.percentile(final, 50)) - current) / current * 100, 1),
                description=f"Mediana esperada con retorno anual ~{ann_ret:.1f}% y volatilidad ~{ann_vol:.1f}%.",
            ),
            ScenarioOutcome(
                name="Alcista",
                horizon_months=horizon_months,
                projected_value=round(float(np.percentile(final, 90)), 2),
                return_pct=round((float(np.percentile(final, 90)) - current) / current * 100, 1),
                description=f"Escenario optimista (percentil 90) tras {horizon_months} meses.",
            ),
        ]

        pos_count = len(portfolio.positions)
        summary = (
            f"Simulación Monte Carlo ({n_sims} trayectorias) sobre ${current:,.2f} "
            f"{'con ' + str(pos_count) + ' posiciones' if pos_count else 'en efectivo'}. "
            f"Horizonte {horizon_months} meses — mediana proyectada ${scenarios[1].projected_value:,.2f} "
            f"({scenarios[1].return_pct:+.1f}%). Rango p10–p90: "
            f"${scenarios[0].projected_value:,.2f} a ${scenarios[2].projected_value:,.2f}."
        )

        return PortfolioProjectionReport(
            portfolio_id=portfolio.id,
            mode=portfolio.mode.value,
            current_value=round(current, 2),
            initial_capital=portfolio.initial_capital,
            horizon_months=horizon_months,
            annual_return_pct=ann_ret,
            annual_volatility_pct=ann_vol,
            points=points,
            scenarios=scenarios,
            summary=summary,
        )

    async def simulate_proposal_impact(
        self,
        portfolio: Portfolio,
        proposal_budget: float,
        expected_return_pct: float = 12.0,
        horizon_months: int = 6,
    ) -> PortfolioProjectionReport:
        """Simulate deploying proposal budget in demo mode."""
        budget = min(proposal_budget, portfolio.cash)
        combined_value = portfolio.total_value
        hypothetical = combined_value - budget + budget * (1 + expected_return_pct / 100 * horizon_months / 12)

        fake = portfolio.model_copy(deep=True)
        fake.cash = max(0, portfolio.cash - budget)
        base_report = await self.project(fake, horizon_months=horizon_months)
        uplift = hypothetical - combined_value
        base_report.summary = (
            f"Simulación demo: desplegar ${budget:,.2f} con retorno esperado {expected_return_pct:.1f}% "
            f"en {horizon_months} meses elevaría el valor a ~${hypothetical:,.2f} "
            f"({uplift / combined_value * 100:+.1f}% vs hoy). "
            + base_report.summary
        )
        return base_report
