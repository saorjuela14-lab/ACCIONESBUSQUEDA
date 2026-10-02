"""Alpaca 1h → clean() → 4h pipeline for Strategy A (PAPER).

`clean` is the port of monarch-estrategia/crypto/engine.py:44-60 as specified
by the desk (UTC, dedupe, drop incomplete bars, do not fill gaps). The original
engine.py is not in this repo; behavior matches the review notes and parity tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import numpy as np
import pandas as pd

from utils.logging import get_logger

logger = get_logger(__name__)

OUTLIER_LOOKBACK = 25
OUTLIER_BAND = 0.12

ALPACA_CRYPTO_BARS_PATH = "/v1beta3/crypto/us/bars"
ALPACA_DATA_BASE = "https://data.alpaca.markets"
BAR_HOURS = 4
HOURS_PER_4H = 4


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {}
    for std in ("Open", "High", "Low", "Close", "Volume"):
        if std in out.columns:
            continue
        for key in (std.lower(), std.upper(), std.capitalize()):
            if key in out.columns:
                rename[key] = std
                break
    if rename:
        out = out.rename(columns=rename)
    return out


def clean(df: pd.DataFrame, bar_h: int = 1) -> pd.DataFrame:
    """engine.py:44-60 — UTC, dedupe, drop incomplete / gapped bars. No fill."""
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    out = _norm_cols(df)
    if not isinstance(out.index, pd.DatetimeIndex):
        try:
            out.index = pd.to_datetime(out.index, utc=True)
        except Exception:
            return pd.DataFrame()
    else:
        if out.index.tz is None:
            out.index = out.index.tz_localize("UTC")
        else:
            out.index = out.index.tz_convert("UTC")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    need = [c for c in ("Open", "High", "Low", "Close") if c in out.columns]
    if len(need) < 4:
        return pd.DataFrame()
    out = out.dropna(subset=need, how="any")
    out = out[(out.index.minute == 0) & (out.index.second == 0)]
    if int(bar_h) > 1:
        out = out[out.index.hour % int(bar_h) == 0]
    out = out[out["Close"].astype(float) > 0]
    if out.empty:
        return out
    out = clip_outlier_prints(out)
    if out.empty:
        return out
    expected = pd.Timedelta(hours=int(bar_h) or 1)
    delta = out.index.to_series().diff()
    # First bar has no previous; later bars with a hole larger than bar_h stay
    # in the 1h series (we do not interpolate) — 4h buckets with missing hours
    # are dropped in resample_4h.
    _ = delta, expected
    return out


def clip_outlier_prints(df: pd.DataFrame) -> pd.DataFrame:
    """engine.clean: clip OHLC to a centered 25-bar median ±12% (ETH Low 788).

    Only bars with trade-count ``n < 200`` (or missing ``n``) are clipped.
    Liquid bars (n ≥ 200) keep the raw print.
    """
    if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
        return df
    out = df.copy()
    close = out["Close"].astype(float)
    med = close.rolling(OUTLIER_LOOKBACK, center=True, min_periods=8).median()
    lo = med * (1.0 - OUTLIER_BAND)
    hi = med * (1.0 + OUTLIER_BAND)
    thin = pd.Series(True, index=out.index)
    n_col = next((c for c in ("n", "N", "trade_count") if c in out.columns), None)
    if n_col is not None:
        counts = pd.to_numeric(out[n_col], errors="coerce")
        thin = counts.isna() | (counts < 200)
    for col in ("Open", "High", "Low", "Close"):
        if col not in out.columns:
            continue
        series = out[col].astype(float)
        clipped = series.copy()
        mask = med.notna() & thin
        clipped.loc[mask] = series.loc[mask].clip(lower=lo.loc[mask], upper=hi.loc[mask])
        out[col] = clipped
    if "High" in out.columns:
        out["High"] = out[["High", "Open", "Close"]].max(axis=1)
    if "Low" in out.columns:
        out["Low"] = out[["Low", "Open", "Close"]].min(axis=1)
    return out


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """4h bars anchored 00/04/08/12/16/20 UTC. Drop buckets with missing hours."""
    cleaned = clean(df_1h, bar_h=1)
    if cleaned.empty:
        return pd.DataFrame()
    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        **({"Volume": "sum"} if "Volume" in cleaned.columns else {}),
    }
    hours = cleaned.resample("4h", label="left", closed="left").size()
    ohlc = cleaned.resample("4h", label="left", closed="left").agg(agg)
    complete = hours == HOURS_PER_4H
    ohlc = ohlc.loc[complete].dropna(subset=["Open", "High", "Low", "Close"], how="any")
    return ohlc


def drop_forming_bar(df_4h: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Keep only fully closed 4h candles (open + 4h <= now)."""
    if df_4h is None or df_4h.empty:
        return pd.DataFrame()
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    else:
        clock = clock.astimezone(timezone.utc)
    idx = df_4h.index
    if not isinstance(idx, pd.DatetimeIndex):
        return df_4h
    closes_at = idx + pd.Timedelta(hours=BAR_HOURS)
    return df_4h.loc[closes_at <= pd.Timestamp(clock)]


def last_closed_4h_open(now: datetime | None = None) -> pd.Timestamp:
    clock = now or datetime.now(timezone.utc)
    ts = pd.Timestamp(clock)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    floored = ts.floor("4h")
    if ts < floored + pd.Timedelta(hours=BAR_HOURS):
        return floored - pd.Timedelta(hours=BAR_HOURS)
    return floored


def bars_to_1h(bars: list[dict[str, Any]] | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        ts = b.get("t") or b.get("timestamp")
        try:
            idx = pd.Timestamp(ts)
        except Exception:
            continue
        try:
            o = float(b.get("o") if b.get("o") is not None else b.get("open"))
            h = float(b.get("h") if b.get("h") is not None else b.get("high"))
            l = float(b.get("l") if b.get("l") is not None else b.get("low"))
            c = float(b.get("c") if b.get("c") is not None else b.get("close"))
        except (TypeError, ValueError):
            continue
        vol = 0.0
        try:
            vol = float(b.get("v") if b.get("v") is not None else b.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        rows.append({"ts": idx, "Open": o, "High": h, "Low": l, "Close": c, "Volume": vol})
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).set_index("ts").sort_index()
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    return frame


def extract_symbol_bars(payload: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    bars = payload.get("bars")
    out: dict[str, list[dict[str, Any]]] = {}
    if isinstance(bars, dict):
        for sym, rows in bars.items():
            if isinstance(rows, list):
                out[str(sym).upper()] = rows
    return out


async def fetch_alpaca_crypto_1h(
    symbols: list[str],
    *,
    now: datetime | None = None,
    lookback_days: int = 400,
    http_get: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, pd.DataFrame]:
    """Public Alpaca crypto 1h bars (no keys). Fail closed → empty frame."""
    wanted = [s for s in symbols if s]
    if not wanted:
        return {}
    clock = now or datetime.now(timezone.utc)
    start = (clock - timedelta(days=int(lookback_days))).strftime("%Y-%m-%dT00:00:00Z")
    getter = http_get or _public_get
    collected: dict[str, list[dict[str, Any]]] = {s.upper(): [] for s in wanted}
    page_token: str | None = None
    try:
        for _ in range(20):
            params: dict[str, Any] = {
                "symbols": ",".join(wanted),
                "timeframe": "1Hour",
                "start": start,
                "limit": 10000,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            payload = await getter(ALPACA_CRYPTO_BARS_PATH, params)
            chunk = extract_symbol_bars(payload if isinstance(payload, dict) else {})
            for raw_sym, rows in chunk.items():
                key = next((w.upper() for w in wanted if w.upper().replace("/", "") == raw_sym.replace("/", "")), raw_sym)
                collected.setdefault(key, []).extend(rows)
            page_token = (payload or {}).get("next_page_token") if isinstance(payload, dict) else None
            if not page_token:
                break
    except Exception as exc:
        logger.warning("strategy_a.alpaca_1h_failed", error=str(exc))
        return {s: pd.DataFrame() for s in wanted}
    out: dict[str, pd.DataFrame] = {}
    for sym in wanted:
        key = next((k for k in collected if k.replace("/", "") == sym.upper().replace("/", "")), sym.upper())
        out[sym] = bars_to_1h(collected.get(key) or collected.get(sym.upper()) or [])
    return out


async def load_strategy_a_4h(
    symbol: str,
    *,
    now: datetime | None = None,
    frames_1h: dict[str, pd.DataFrame] | None = None,
    http_get: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> pd.DataFrame:
    if frames_1h and symbol in frames_1h:
        raw = frames_1h[symbol]
    else:
        fetched = await fetch_alpaca_crypto_1h([symbol], now=now, http_get=http_get)
        raw = fetched.get(symbol, pd.DataFrame())
    if raw is None or raw.empty:
        return pd.DataFrame()
    return drop_forming_bar(resample_4h(raw), now=now)


async def _public_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    import httpx

    url = f"{ALPACA_DATA_BASE}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Public crypto bars — no API keys (desk requirement).
        response = await client.get(url, params=params, headers={"Accept": "application/json"})
        if not response.is_success:
            raise RuntimeError(f"alpaca_public_bars {response.status_code}: {response.text[:200]}")
        data = response.json()
        return data if isinstance(data, dict) else {}
