"""Build OHLC + indicator series for dashboard charts."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import numpy as np
import pandas as pd

from agents.technical.gaps import (
    GAP_TIMEFRAME_BY_LABEL,
    GAP_TIMEFRAME_CONFIG,
    VALID_CHART_TIMEFRAMES,
    detect_gaps,
    resample_ohlc,
)
from agents.technical.indicators import build_trade_levels, detect_support_resistance, enrich_indicators
from domain.dashboard import PriceGap, TechnicalChartData, TechnicalChartPoint, TechnicalSnapshot
from providers.interfaces import MarketDataProvider
from providers.market.intervals import (
    PERIOD_DAYS,
    assess_market_status,
    longer_period,
)
from utils.logging import get_logger
from utils.narrative_es import bias_label

logger = get_logger(__name__)

_INTRADAY_INTERVALS = frozenset({"1h", "30m", "15m", "5m"})


def _format_point_time(idx, interval: str) -> str:
    if hasattr(idx, "strftime"):
        if interval in _INTRADAY_INTERVALS:
            return idx.strftime("%Y-%m-%d %H:%M")
        return idx.strftime("%Y-%m-%d")
    return str(idx)[:16]


def _trim_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Keep bars inside the user-selected lookback ending at the last available bar."""
    if df.empty or period not in PERIOD_DAYS:
        return df
    last = df.index[-1]
    try:
        last_ts = pd.Timestamp(last)
    except Exception:
        return df
    # Use last bar as end so delisted history still shows the selected window of
    # available data instead of an empty chart relative to "today".
    if last_ts.tzinfo is not None:
        cutoff = last_ts - pd.Timedelta(days=PERIOD_DAYS[period])
    else:
        cutoff = last_ts - timedelta(days=PERIOD_DAYS[period])
    trimmed = df[df.index >= cutoff]
    return trimmed if len(trimmed) >= 5 else df


class TechnicalChartService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def build(
        self,
        ticker: str,
        period: str = "6mo",
        chart_timeframe: str = "1D",
    ) -> TechnicalChartData:
        ticker = ticker.upper()
        tf = chart_timeframe if chart_timeframe in VALID_CHART_TIMEFRAMES else "1D"

        df, interval = await self._load_ohlc(ticker, tf, period)
        min_bars = 20 if tf not in ("1D", "1W") else 30
        status, stale_days, as_of = assess_market_status(df, interval)

        if df.empty or len(df) < min_bars:
            gaps_by_tf = await self._scan_gaps_all_timeframes(ticker)
            # Daily empty for a delisted name — probe weekly for last known session
            if status == "unavailable":
                probe_as_of, probe_days, probe_status = await self._probe_last_known(ticker)
                if probe_as_of:
                    as_of, stale_days, status = probe_as_of, probe_days, probe_status
            summary = self._insufficient_summary(ticker, tf, status, as_of, stale_days)
            return TechnicalChartData(
                ticker=ticker,
                period=period,
                chart_timeframe=tf,
                summary=summary,
                gaps_by_timeframe=gaps_by_tf,
                as_of=as_of,
                stale_days=stale_days,
                market_status=status if status != "live" else "unavailable",
            )

        enriched = enrich_indicators(df)
        # Display window follows the UI period; indicators used the longer fetch.
        display = _trim_to_period(enriched, period)
        levels = detect_support_resistance(enriched)
        last = enriched.iloc[-1]
        price = float(last["Close"])
        atr = float(last["ATR"]) if pd.notna(last.get("ATR")) else price * 0.02
        trade_levels = build_trade_levels(
            price,
            levels["support"],
            levels["resistance"],
            atr,
        )

        points: list[TechnicalChartPoint] = []
        for idx, row in display.iterrows():
            date_str = _format_point_time(idx, interval)
            vol = float(row["Volume"]) if "Volume" in row and pd.notna(row["Volume"]) else None
            points.append(
                TechnicalChartPoint(
                    date=date_str,
                    open=round(float(row["Open"]), 4),
                    high=round(float(row["High"]), 4),
                    low=round(float(row["Low"]), 4),
                    close=round(float(row["Close"]), 4),
                    volume=vol,
                    sma20=self._f(row.get("SMA20")),
                    sma50=self._f(row.get("SMA50")),
                    ema20=self._f(row.get("EMA20")),
                    bb_upper=self._f(row.get("BB_Upper")),
                    bb_lower=self._f(row.get("BB_Lower")),
                    rsi=self._f(row.get("RSI")),
                    macd=self._f(row.get("MACD")),
                    macd_signal=self._f(row.get("MACD_Signal")),
                    macd_hist=self._f(row.get("MACD_Hist")),
                )
            )

        rsi = self._f(last.get("RSI"))
        macd = self._f(last.get("MACD"))
        macd_sig = self._f(last.get("MACD_Signal"))
        sma20 = self._f(last.get("SMA20"))
        sma50 = self._f(last.get("SMA50"))

        bias = "neutral"
        score = 0.0
        if rsi is not None:
            if rsi < 30:
                score += 2
            elif rsi > 70:
                score -= 2
        if macd is not None and macd_sig is not None:
            score += 1.5 if macd > macd_sig else -1.5
        if sma20 is not None:
            score += 1 if price > sma20 else -1
        if sma50 is not None:
            score += 1 if price > sma50 else -1
        bias = "bullish" if score >= 1 else "bearish" if score <= -1 else "neutral"

        snapshot = TechnicalSnapshot(
            price=round(price, 2),
            rsi=rsi,
            macd=macd,
            macd_signal=macd_sig,
            macd_hist=self._f(last.get("MACD_Hist")),
            sma20=sma20,
            sma50=sma50,
            ema20=self._f(last.get("EMA20")),
            atr=round(atr, 2),
            bias=bias,
            support=round(levels["support"], 2),
            resistance=round(levels["resistance"], 2),
            stop_loss=round(trade_levels.get("stop_loss", 0), 2) if trade_levels.get("stop_loss") else None,
            take_profit_1=round(trade_levels.get("take_profit_1", 0), 2) if trade_levels.get("take_profit_1") else None,
            risk_reward=trade_levels.get("risk_reward_ratio"),
        )

        gaps_by_tf = await self._scan_gaps_all_timeframes(ticker)
        chart_gaps = gaps_by_tf.get(tf, detect_gaps(df, timeframe=tf, min_gap_pct=GAP_TIMEFRAME_BY_LABEL[tf][3], interval=interval))
        unfilled = [g for gaps in gaps_by_tf.values() for g in gaps if not g.filled]

        tf_label = {"1D": "diario", "1W": "semanal", "4H": "4 horas", "1H": "1 hora", "30m": "30 min", "15m": "15 min"}.get(tf, tf)
        summary = self._build_summary(ticker, snapshot, bias, unfilled, tf_label, status, as_of, stale_days)

        return TechnicalChartData(
            ticker=ticker,
            period=period,
            chart_timeframe=tf,
            points=points,
            snapshot=snapshot,
            trade_levels=trade_levels,
            summary=summary,
            gaps=chart_gaps,
            gaps_by_timeframe=gaps_by_tf,
            unfilled_gaps=unfilled,
            as_of=as_of,
            stale_days=stale_days,
            market_status=status,
        )

    async def _load_ohlc(self, ticker: str, chart_timeframe: str, period: str) -> tuple[pd.DataFrame, str]:
        label, hist_period, interval, _, resample_rule = GAP_TIMEFRAME_BY_LABEL[chart_timeframe]
        # Fetch enough history for indicators (e.g. SMA50 weekly) while honoring a
        # longer user-selected period when requested.
        fetch_period = longer_period(hist_period, period)
        df = await self._market.get_history(ticker, period=fetch_period, interval=interval)
        if resample_rule and not df.empty:
            df = resample_ohlc(df, resample_rule)
        return df, interval if not resample_rule else resample_rule

    async def _probe_last_known(self, ticker: str) -> tuple[str | None, int | None, str]:
        """When the requested TF is empty, find any last known bar for messaging."""
        for hist_period, interval in (("5y", "1wk"), ("2y", "1d")):
            try:
                df = await self._market.get_history(ticker, period=hist_period, interval=interval)
            except Exception:
                continue
            status, stale_days, as_of = assess_market_status(df, interval)
            if as_of:
                # No usable series for the requested TF → treat as delisted/unavailable
                if status == "live":
                    status = "stale"
                return as_of, stale_days, status if status != "unavailable" else "delisted"
        return None, None, "unavailable"

    async def _scan_gaps_all_timeframes(self, ticker: str) -> dict[str, list[PriceGap]]:
        async def _scan_one(label: str, hist_period: str, interval: str, min_pct: float, resample: str | None):
            try:
                df = await self._market.get_history(ticker, period=hist_period, interval=interval)
                if df.empty or len(df) < 5:
                    return label, []
                if resample:
                    df = resample_ohlc(df, resample)
                if len(df) < 2:
                    return label, []
                return label, detect_gaps(df, timeframe=label, min_gap_pct=min_pct, interval=interval)
            except Exception as exc:
                logger.warning("gaps.scan_failed", ticker=ticker, tf=label, error=str(exc))
                return label, []

        results = await asyncio.gather(
            *[_scan_one(*cfg) for cfg in GAP_TIMEFRAME_CONFIG]
        )
        return {label: gaps for label, gaps in results}

    def _f(self, val) -> float | None:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        try:
            return round(float(val), 4)
        except (TypeError, ValueError):
            return None

    def _freshness_prefix(
        self,
        ticker: str,
        status: str,
        as_of: str | None,
        stale_days: int | None,
    ) -> str:
        if status == "delisted":
            age = f" (hace {stale_days} días)" if stale_days is not None else ""
            return (
                f"⚠ {ticker} sin cotización actual — parece deslistada o suspendida. "
                f"Último dato de mercado: {as_of or 'desconocido'}{age}. "
                "No hay serie completa a día de hoy; el análisis siguiente usa solo historia residual."
            )
        if status == "stale":
            age = f" (hace {stale_days} días)" if stale_days is not None else ""
            return (
                f"⚠ Datos desactualizados para {ticker}: última vela {as_of or 'desconocida'}{age}. "
                "El mercado en vivo a día de hoy no está reflejado por completo."
            )
        return ""

    def _insufficient_summary(
        self,
        ticker: str,
        tf: str,
        status: str,
        as_of: str | None,
        stale_days: int | None,
    ) -> str:
        prefix = self._freshness_prefix(ticker, status, as_of, stale_days)
        if prefix:
            return f"{prefix} Datos insuficientes para {tf}."
        if as_of:
            return (
                f"Datos insuficientes para {tf} de {ticker}. "
                f"Última sesión conocida: {as_of}."
            )
        return (
            f"Datos insuficientes para {tf} de {ticker}. "
            "Sin histórico reciente a día de hoy (ticker inexistente, deslistado o sin cobertura)."
        )

    def _build_summary(
        self,
        ticker: str,
        snap: TechnicalSnapshot,
        bias: str,
        unfilled: list[PriceGap],
        tf_label: str,
        status: str,
        as_of: str | None,
        stale_days: int | None,
    ) -> str:
        parts: list[str] = []
        prefix = self._freshness_prefix(ticker, status, as_of, stale_days)
        if prefix:
            parts.append(prefix)
        elif as_of:
            parts.append(f"Datos a {as_of} (mercado al día).")

        parts.extend(
            [
                f"Análisis técnico {tf_label} de {ticker}: sesgo {bias_label(bias)}.",
                f"Precio ${snap.price}, RSI {snap.rsi or 'N/D'}, MACD {'alcista' if snap.macd and snap.macd_signal and snap.macd > snap.macd_signal else 'bajista' if snap.macd and snap.macd_signal else 'N/D'}.",
                f"SMA20 ${snap.sma20 or '—'}, SMA50 ${snap.sma50 or '—'}.",
                f"Soporte ${snap.support}, resistencia ${snap.resistance}.",
            ]
        )
        if snap.stop_loss and snap.take_profit_1:
            parts.append(
                f"Stop ${snap.stop_loss}, objetivo ${snap.take_profit_1}"
                + (f" (R/R {snap.risk_reward}x)" if snap.risk_reward else "")
                + "."
            )
        open_gaps = [g for g in unfilled if not g.filled]
        if open_gaps and status == "live":
            gap_lines = []
            for g in open_gaps[:5]:
                dir_es = "↑" if g.gap_type == "gap_up" else "↓"
                gap_lines.append(
                    f"{g.timeframe} {dir_es} ${g.gap_bottom}–${g.gap_top} (fill → ${g.fill_target})"
                )
            parts.append(
                f"Gaps sin cubrir ({len(open_gaps)}): el mercado suele buscar fill. "
                + "; ".join(gap_lines)
                + ("…" if len(open_gaps) > 5 else "")
                + "."
            )
        elif open_gaps and status != "live":
            parts.append(
                f"Se detectaron {len(open_gaps)} gaps históricos; no aplican como setup en vivo "
                "mientras no haya cotización actual."
            )
        return " ".join(parts)
