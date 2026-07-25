"""Market structure: swing highs/lows → uptrend / downtrend / range."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _swing_points(series: pd.Series, left: int = 3, right: int = 3) -> list[tuple[Any, float, str]]:
    """Return (index, value, kind) for local highs/lows."""
    vals = series.to_numpy(dtype=float)
    idx = series.index
    swings: list[tuple[Any, float, str]] = []
    n = len(vals)
    for i in range(left, n - right):
        window = vals[i - left : i + right + 1]
        if np.isnan(vals[i]):
            continue
        if vals[i] == np.nanmax(window) and np.sum(window == vals[i]) == 1:
            swings.append((idx[i], float(vals[i]), "high"))
        elif vals[i] == np.nanmin(window) and np.sum(window == vals[i]) == 1:
            swings.append((idx[i], float(vals[i]), "low"))
    return swings


def classify_structure(df: pd.DataFrame, lookback: int = 80) -> dict[str, Any]:
    """
    Classify trend via recent swing highs/lows.

    Uptrend: higher highs + higher lows
    Downtrend: lower highs + lower lows
    Else: range / mixed
    """
    if df.empty or len(df) < 20:
        return {
            "structure": "range",
            "label_es": "lateral / indefinida",
            "swings": [],
            "last_high": None,
            "last_low": None,
            "confidence": 0.3,
        }

    window = df.tail(lookback)
    highs = _swing_points(window["High"])
    lows = _swing_points(window["Low"])
    recent_highs = [h for h in highs if h[2] == "high"][-3:]
    recent_lows = [lo for lo in lows if lo[2] == "low"][-3:]

    hh = hl = lh = ll = 0
    if len(recent_highs) >= 2:
        for a, b in zip(recent_highs, recent_highs[1:]):
            if b[1] > a[1]:
                hh += 1
            elif b[1] < a[1]:
                lh += 1
    if len(recent_lows) >= 2:
        for a, b in zip(recent_lows, recent_lows[1:]):
            if b[1] > a[1]:
                hl += 1
            elif b[1] < a[1]:
                ll += 1

    if hh >= 1 and hl >= 1 and lh == 0 and ll == 0:
        structure = "uptrend"
        label_es = "alcista (HH/HL)"
        confidence = 0.75
    elif lh >= 1 and ll >= 1 and hh == 0 and hl == 0:
        structure = "downtrend"
        label_es = "bajista (LH/LL)"
        confidence = 0.75
    elif hh + hl > lh + ll:
        structure = "uptrend"
        label_es = "alcista débil / mixtas"
        confidence = 0.55
    elif lh + ll > hh + hl:
        structure = "downtrend"
        label_es = "bajista débil / mixtas"
        confidence = 0.55
    else:
        structure = "range"
        label_es = "lateral / rango"
        confidence = 0.5

    last_high = recent_highs[-1][1] if recent_highs else None
    last_low = recent_lows[-1][1] if recent_lows else None

    swings = [
        {
            "date": str(i)[:16],
            "price": round(v, 4),
            "kind": k,
        }
        for i, v, k in (recent_highs + recent_lows)[-6:]
    ]

    return {
        "structure": structure,
        "label_es": label_es,
        "swings": swings,
        "last_high": round(last_high, 4) if last_high is not None else None,
        "last_low": round(last_low, 4) if last_low is not None else None,
        "confidence": confidence,
        "pattern_counts": {"hh": hh, "hl": hl, "lh": lh, "ll": ll},
    }
