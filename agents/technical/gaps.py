"""Price gap detection across timeframes — unfilled gaps tend to get filled."""

from __future__ import annotations

import pandas as pd

from domain.dashboard import PriceGap


def _format_time(idx, interval: str) -> str:
    if hasattr(idx, "strftime"):
        if interval in ("1h", "30m", "15m", "5m"):
            return idx.strftime("%Y-%m-%d %H:%M")
        return idx.strftime("%Y-%m-%d")
    return str(idx)[:16]


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV (e.g. 1h → 4h)."""
    if df.empty:
        return df
    out = df.resample(rule).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["Open", "Close"])
    return out


def detect_gaps(
    df: pd.DataFrame,
    timeframe: str,
    min_gap_pct: float = 0.2,
    interval: str = "1d",
) -> list[PriceGap]:
    """
    Detect price gaps where open gaps away from previous close.

    A gap is unfilled until price trades back into the gap zone.
    """
    if df.empty or len(df) < 2:
        return []

    gaps: list[PriceGap] = []
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        prev_close = float(prev["Close"])
        curr_open = float(curr["Open"])
        curr_high = float(curr["High"])
        curr_low = float(curr["Low"])

        if prev_close <= 0:
            continue

        gap_pct = abs(curr_open - prev_close) / prev_close * 100
        if gap_pct < min_gap_pct:
            continue

        idx = df.index[i]
        date_str = _format_time(idx, interval)

        if curr_open > prev_close:
            # Gap alcista — zona entre cierre previo y apertura actual
            if curr_low <= prev_close:
                continue  # no hay espacio vacío real
            gap_type = "gap_up"
            gap_bottom = round(prev_close, 4)
            gap_top = round(curr_open, 4)
            fill_target = gap_bottom
        elif curr_open < prev_close:
            # Gap bajista
            if curr_high >= prev_close:
                continue
            gap_type = "gap_down"
            gap_top = round(prev_close, 4)
            gap_bottom = round(curr_open, 4)
            fill_target = gap_top
        else:
            continue

        filled, filled_date = _check_filled(df.iloc[i + 1:], gap_bottom, gap_top)

        gaps.append(
            PriceGap(
                timeframe=timeframe,
                date=date_str,
                gap_type=gap_type,
                gap_top=gap_top,
                gap_bottom=gap_bottom,
                gap_size_pct=round(gap_pct, 2),
                gap_size_abs=round(abs(gap_top - gap_bottom), 4),
                fill_target=fill_target,
                filled=filled,
                filled_date=filled_date,
                note=_gap_note(gap_type, filled, fill_target),
            )
        )

    return gaps


def _check_filled(
    future: pd.DataFrame,
    gap_bottom: float,
    gap_top: float,
) -> tuple[bool, str | None]:
    """Gap is filled when price trades into the gap zone."""
    for idx, row in future.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        if low <= gap_top and high >= gap_bottom:
            filled_at = _format_time(idx, "1d")
            return True, filled_at
    return False, None


def _gap_note(gap_type: str, filled: bool, fill_target: float) -> str:
    direction = "alcista" if gap_type == "gap_up" else "bajista"
    if filled:
        return f"Gap {direction} cubierto."
    return f"Gap {direction} sin cubrir — objetivo de fill hacia ${fill_target:.2f}."


# (label, period, interval, min_gap_pct, resample_rule?)
GAP_TIMEFRAME_CONFIG: list[tuple[str, str, str, float, str | None]] = [
    ("1D", "1y", "1d", 0.25, None),
    ("1W", "5y", "1wk", 0.5, None),
    ("4H", "3mo", "1h", 0.15, "4h"),
    ("1H", "1mo", "1h", 0.12, None),
    ("30m", "1mo", "30m", 0.1, None),
    ("15m", "5d", "15m", 0.08, None),
]

GAP_TIMEFRAME_BY_LABEL: dict[str, tuple[str, str, str, float, str | None]] = {
    cfg[0]: cfg for cfg in GAP_TIMEFRAME_CONFIG
}

VALID_CHART_TIMEFRAMES = list(GAP_TIMEFRAME_BY_LABEL.keys())
