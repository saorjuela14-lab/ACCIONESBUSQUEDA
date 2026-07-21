"""Portfolio risk metrics — approx VaR, beta, sector concentration."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

from domain.ops import PortfolioRiskMetrics
from providers.interfaces import MarketDataProvider
from providers.market.factory import get_market_provider
from utils.logging import get_logger

logger = get_logger(__name__)


class PortfolioRiskMetricsService:
    """Lightweight firm risk metrics for hard gates (not a full risk engine)."""

    def __init__(self, market: MarketDataProvider | None = None) -> None:
        self._market = market or get_market_provider()

    async def compute(
        self,
        positions: Sequence[Any],
        *,
        equity: float,
        benchmark: str = "SPY",
    ) -> PortfolioRiskMetrics:
        warnings: list[str] = []
        if equity <= 0 or not positions:
            return PortfolioRiskMetrics(equity=equity, warnings=["Sin posiciones / equity"])

        weights: dict[str, float] = {}
        betas: list[tuple[float, float]] = []  # (weight, beta)
        sector_w: dict[str, float] = {}
        returns_series: list[pd.Series] = []
        w_list: list[float] = []

        for p in positions:
            data = p.model_dump() if hasattr(p, "model_dump") else dict(p)
            sym = str(data.get("symbol") or data.get("ticker") or "").upper()
            mv = float(data.get("market_value") or 0)
            if not sym or mv <= 0:
                continue
            w = mv / equity
            weights[sym] = w

            sector = data.get("sector")
            beta = data.get("beta")
            try:
                quote = await self._market.get_quote(sym)
                sector = sector or quote.get("sector") or "Unknown"
                if beta is None and quote.get("beta") is not None:
                    beta = float(quote["beta"])
            except Exception:
                sector = sector or "Unknown"

            sector_w[sector] = sector_w.get(sector, 0.0) + w * 100
            if beta is not None:
                try:
                    betas.append((w, float(beta)))
                except (TypeError, ValueError):
                    pass

            try:
                hist = await self._market.get_history(sym, period="3mo", interval="1d")
                if not hist.empty and "Close" in hist.columns:
                    rets = hist["Close"].pct_change().dropna()
                    returns_series.append(rets.rename(sym))
                    w_list.append(w)
            except Exception as exc:
                warnings.append(f"{sym}: hist falló ({exc})")

        port_beta = None
        if betas:
            port_beta = round(sum(w * b for w, b in betas) / max(sum(w for w, _ in betas), 1e-9), 3)

        var_pct = None
        var_usd = None
        if returns_series and w_list:
            try:
                aligned = pd.concat(returns_series, axis=1).dropna(how="any")
                if len(aligned) >= 20:
                    w_arr = np.array(w_list[: aligned.shape[1]], dtype=float)
                    w_arr = w_arr / w_arr.sum()
                    port_rets = aligned.values @ w_arr
                    var_pct = float(-np.percentile(port_rets, 5) * 100)  # 95% 1d VaR as positive %
                    var_usd = round(equity * var_pct / 100.0, 2)
                    var_pct = round(var_pct, 2)
            except Exception as exc:
                warnings.append(f"VaR calc falló: {exc}")

        max_sector = None
        max_sector_pct = 0.0
        if sector_w:
            max_sector = max(sector_w, key=sector_w.get)
            max_sector_pct = round(sector_w[max_sector], 2)

        return PortfolioRiskMetrics(
            equity=equity,
            var_1d_95_pct=var_pct,
            var_1d_95_usd=var_usd,
            portfolio_beta=port_beta,
            sector_weights={k: round(v, 2) for k, v in sector_w.items()},
            max_sector=max_sector,
            max_sector_pct=max_sector_pct,
            warnings=warnings,
        )

    def gate_buy(
        self,
        *,
        metrics: PortfolioRiskMetrics,
        symbol: str,
        notional: float,
        sector: str | None,
        beta: float | None,
        max_var_pct: float,
        max_beta: float,
        max_sector_pct: float,
    ) -> tuple[bool, list[str]]:
        """Hard gate: return (allowed, reasons)."""
        reasons: list[str] = []
        if metrics.var_1d_95_pct is not None and metrics.var_1d_95_pct > max_var_pct:
            reasons.append(
                f"VaR 1d 95% del libro {metrics.var_1d_95_pct:.1f}% > límite {max_var_pct:.1f}%."
            )
        if metrics.portfolio_beta is not None and metrics.portfolio_beta > max_beta:
            reasons.append(
                f"Beta del portafolio {metrics.portfolio_beta:.2f} > límite {max_beta:.2f}."
            )
        if sector and metrics.equity > 0:
            current = metrics.sector_weights.get(sector, 0.0)
            add_pct = notional / metrics.equity * 100
            projected = current + add_pct
            if projected > max_sector_pct + 0.01:
                reasons.append(
                    f"Sector {sector} proyectado {projected:.1f}% > máximo {max_sector_pct:.0f}% "
                    f"(compra {symbol})."
                )
        return (len(reasons) == 0, reasons)
