"""Higher-timeframe trend gate: require weekly + monthly uptrend structure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.technical.structure import classify_structure
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class HtfTrendResult:
    ticker: str
    passed: bool
    weekly: str = "unknown"
    monthly: str = "unknown"
    weekly_confidence: float = 0.0
    monthly_confidence: float = 0.0
    reason: str = ""
    inconclusive: bool = False  # missing/weak data → do not hard-reject

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "passed": self.passed,
            "weekly": self.weekly,
            "monthly": self.monthly,
            "weekly_confidence": self.weekly_confidence,
            "monthly_confidence": self.monthly_confidence,
            "reason": self.reason,
            "inconclusive": self.inconclusive,
        }


class HtfTrendFilter:
    """PASO 01 gate: keep names with strong weekly AND monthly uptrend."""

    def __init__(self, market: MarketDataProvider, *, min_confidence: float = 0.5) -> None:
        self._market = market
        self._min_confidence = min_confidence

    async def evaluate(self, ticker: str) -> HtfTrendResult:
        sym = ticker.upper().strip()
        weekly = await self._structure(sym, period="5y", interval="1wk")
        monthly = await self._structure(sym, period="10y", interval="1mo")

        w_struct = str(weekly.get("structure") or "unknown")
        m_struct = str(monthly.get("structure") or "unknown")
        w_conf = float(weekly.get("confidence") or 0)
        m_conf = float(monthly.get("confidence") or 0)
        w_ok = bool(weekly.get("ok"))
        m_ok = bool(monthly.get("ok"))

        # Fail-open when bars missing — avoid silent universe wipe on rate limits
        if not w_ok or not m_ok:
            return HtfTrendResult(
                ticker=sym,
                passed=True,
                weekly=w_struct,
                monthly=m_struct,
                weekly_confidence=w_conf,
                monthly_confidence=m_conf,
                reason="HTF inconcluso (sin barras suficientes) — se deja pasar",
                inconclusive=True,
            )

        passed = (
            w_struct == "uptrend"
            and m_struct == "uptrend"
            and w_conf >= self._min_confidence
            and m_conf >= self._min_confidence
        )
        if passed:
            reason = "Uptrend semanal + mensual"
        else:
            reason = f"HTF rechazado (1W={w_struct}/{w_conf:.2f}, 1M={m_struct}/{m_conf:.2f})"

        return HtfTrendResult(
            ticker=sym,
            passed=passed,
            weekly=w_struct,
            monthly=m_struct,
            weekly_confidence=w_conf,
            monthly_confidence=m_conf,
            reason=reason,
            inconclusive=False,
        )

    async def _structure(self, ticker: str, *, period: str, interval: str) -> dict[str, Any]:
        try:
            df = await self._market.get_history(ticker, period=period, interval=interval)
            if df is None or getattr(df, "empty", True) or len(df) < 20:
                return {"structure": "unknown", "confidence": 0.0, "ok": False}
            out = classify_structure(df)
            out["ok"] = True
            return out
        except Exception as exc:
            logger.warning("htf_trend.fetch_failed", ticker=ticker, interval=interval, error=str(exc))
            return {"structure": "unknown", "confidence": 0.0, "ok": False}
