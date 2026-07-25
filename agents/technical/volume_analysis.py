"""Volume confirmation helpers (relative volume, VWAP bias)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def analyze_volume(df: pd.DataFrame) -> dict[str, Any]:
    """Relative volume vs 20-bar average + VWAP location."""
    if df.empty or "Volume" not in df.columns:
        return {
            "volume_ratio": None,
            "volume_confirm": "unknown",
            "volume_confirm_es": "volumen no disponible",
            "above_vwap": None,
            "vwap": None,
        }

    last = df.iloc[-1]
    vol = float(last["Volume"]) if pd.notna(last.get("Volume")) else 0.0
    vol_sma = float(last["Volume_SMA20"]) if "Volume_SMA20" in df.columns and pd.notna(last.get("Volume_SMA20")) else None
    if vol_sma is None and len(df) >= 5:
        vol_sma = float(df["Volume"].tail(20).mean())

    ratio = round(vol / vol_sma, 2) if vol_sma and vol_sma > 0 else None
    close = float(last["Close"])
    vwap = float(last["VWAP"]) if "VWAP" in df.columns and pd.notna(last.get("VWAP")) else None
    above_vwap = bool(close > vwap) if vwap is not None else None

    if ratio is None:
        confirm, confirm_es = "unknown", "volumen no disponible"
    elif ratio >= 1.2:
        confirm, confirm_es = "expansion", "expansión de volumen (≥20% sobre media)"
    elif ratio <= 0.7:
        confirm, confirm_es = "dry", "volumen seco / bajo interés"
    else:
        confirm, confirm_es = "normal", "volumen en rango normal"

    return {
        "volume_ratio": ratio,
        "volume_confirm": confirm,
        "volume_confirm_es": confirm_es,
        "above_vwap": above_vwap,
        "vwap": round(vwap, 4) if vwap is not None else None,
        "last_volume": vol if vol else None,
    }
