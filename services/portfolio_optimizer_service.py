"""Portfolio mean-variance optimization with constraints."""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from domain.proposal import RiskProfile
from utils.logging import get_logger

logger = get_logger(__name__)

MAX_WEIGHT = 0.25
MAX_SECTOR_WEIGHT = 0.40


class PortfolioOptimizerService:
    """Optimizes weights given expected returns, covariance, and constraints."""

    def optimize(
        self,
        tickers: list[str],
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
        risk_profile: RiskProfile = RiskProfile.BALANCED,
        sector_map: dict[str, str] | None = None,
    ) -> dict[str, float]:
        n = len(tickers)
        if n == 0:
            return {}

        risk_aversion = {"conservative": 3.0, "balanced": 1.5, "aggressive": 0.8}[risk_profile.value]

        def objective(w: np.ndarray) -> float:
            port_return = w @ expected_returns
            port_var = w @ cov_matrix @ w
            return -(port_return - risk_aversion * port_var)

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(0.0, MAX_WEIGHT) for _ in range(n)]

        w0 = np.ones(n) / n
        try:
            result = minimize(objective, w0, method="SLSQP", bounds=bounds, constraints=constraints)
            weights = result.x if result.success else w0
        except Exception as exc:
            logger.warning("optimizer.failed", error=str(exc))
            weights = w0

        weights = np.clip(weights, 0, MAX_WEIGHT)
        weights = weights / weights.sum() if weights.sum() > 0 else w0

        return {t: float(weights[i]) for i, t in enumerate(tickers)}

    @staticmethod
    def build_covariance(returns_matrix: np.ndarray) -> np.ndarray:
        if returns_matrix.shape[0] < 2:
            n = returns_matrix.shape[1]
            return np.eye(n) * 0.04
        return np.cov(returns_matrix, rowvar=False)

    @staticmethod
    def estimate_returns(scores: list[float], confidences: list[float]) -> np.ndarray:
        """Map committee scores to expected annualized returns (simplified)."""
        return np.array([s * c * 0.001 for s, c in zip(scores, confidences)])
