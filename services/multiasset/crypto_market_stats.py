"""Weekly Alpaca 30d ADV + correlation for Strategy A PAPER.

Recalculated once per ISO week together. A name without 30 daily Alpaca bars
does not enter. 1% ADV uses median daily notional (close × volume).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Awaitable

import numpy as np
import pandas as pd

from services.multiasset.crypto_risk import CORR_THRESHOLD, _norm
from utils.logging import get_logger

logger = get_logger(__name__)

FLAG_WEEKLY = "crypto_strategy_a_weekly_market"
MIN_DAILY_BARS = 30
ADV_LOOKBACK_DAYS = 30
CORR_4H_DAYS = 90
CORR_4H_BARS = CORR_4H_DAYS * 6  # 4h × 6 / day
ALPACA_CRYPTO_BARS_PATH = "/v1beta3/crypto/us/bars"


def daily_from_4h(df_4h: pd.DataFrame | None) -> pd.DataFrame:
    if df_4h is None or getattr(df_4h, "empty", True):
        return pd.DataFrame()
    cols = {c.lower(): c for c in df_4h.columns}
    need = {}
    for name in ("Open", "High", "Low", "Close"):
        key = cols.get(name.lower())
        if key is None:
            return pd.DataFrame()
        need[name] = key
    vol_key = cols.get("volume")
    frame = pd.DataFrame({k: df_4h[v].astype(float) for k, v in need.items()})
    if vol_key:
        frame["Volume"] = df_4h[vol_key].astype(float)
    if not isinstance(df_4h.index, pd.DatetimeIndex):
        return pd.DataFrame()
    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        **({"Volume": "sum"} if "Volume" in frame.columns else {}),
    }
    return frame.resample("1D").agg(agg).dropna(how="all")


def median_adv_30d(daily: pd.DataFrame | None) -> tuple[float | None, str]:
    """Median daily notional over the last 30 daily bars. Fail closed without history."""
    if daily is None or getattr(daily, "empty", True):
        return None, "history_lt_30d"
    if len(daily) < MIN_DAILY_BARS:
        return None, "history_lt_30d"
    cols = {c.lower(): c for c in daily.columns}
    close_k = cols.get("close")
    vol_k = cols.get("volume")
    if not close_k or not vol_k:
        return None, "volume_missing"
    close = daily[close_k].astype(float).tail(ADV_LOOKBACK_DAYS)
    vol = daily[vol_k].astype(float).tail(ADV_LOOKBACK_DAYS)
    if len(close) < MIN_DAILY_BARS or len(vol) < MIN_DAILY_BARS:
        return None, "history_lt_30d"
    adv = (close * vol).replace(0, np.nan).dropna()
    if len(adv) < MIN_DAILY_BARS:
        return None, "history_lt_30d"
    med = float(adv.median())
    if not np.isfinite(med) or med <= 0:
        return None, "adv_invalid"
    return med, "ok"


def log_returns_4h(df_4h: pd.DataFrame | None, *, bars: int = CORR_4H_BARS) -> pd.Series | None:
    """90d of 4h log returns for ρ≥0.7 groups (not 30 daily prints)."""
    if df_4h is None or getattr(df_4h, "empty", True):
        return None
    cols = {c.lower(): c for c in df_4h.columns}
    close_k = cols.get("close")
    if not close_k or len(df_4h) < 20:
        return None
    close = df_4h[close_k].astype(float).tail(int(bars) + 1)
    r = np.log(close.replace(0, np.nan)).diff().dropna()
    return r if len(r) >= 20 else None


def daily_log_returns(daily: pd.DataFrame) -> pd.Series | None:
    cols = {c.lower(): c for c in daily.columns}
    close_k = cols.get("close")
    if not close_k or len(daily) < MIN_DAILY_BARS:
        return None
    close = daily[close_k].astype(float).tail(ADV_LOOKBACK_DAYS + 1)
    r = np.log(close.replace(0, np.nan)).diff().dropna()
    return r if len(r) >= 10 else None


def pairwise_corr(returns: dict[str, pd.Series], *, thresh: float = CORR_THRESHOLD) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    names = list(returns)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            sa, sb = returns[a], returns[b]
            aligned = pd.concat([sa, sb], axis=1, join="inner").dropna()
            if len(aligned) < 10:
                continue
            rho = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if np.isfinite(rho):
                out[(_norm(a), _norm(b))] = rho
    _ = thresh
    return out


def alpaca_bars_to_daily(bars: list[dict[str, Any]] | None) -> pd.DataFrame:
    """Parse Alpaca crypto daily bars into an OHLC+Volume frame."""
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
            close = float(b.get("c") if b.get("c") is not None else b.get("close") or 0)
            vol = float(b.get("v") if b.get("v") is not None else b.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        try:
            o = float(b.get("o") if b.get("o") is not None else b.get("open") or close)
            h = float(b.get("h") if b.get("h") is not None else b.get("high") or close)
            l = float(b.get("l") if b.get("l") is not None else b.get("low") or close)
        except (TypeError, ValueError):
            o = h = l = close
        rows.append({"ts": idx, "Open": o, "High": h, "Low": l, "Close": close, "Volume": vol})
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).set_index("ts").sort_index()
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    return frame


def extract_alpaca_symbol_bars(payload: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """Normalize v1beta3 `{bars: {SYM: [...]}}` (and page leftovers) to symbol → bars."""
    if not isinstance(payload, dict):
        return {}
    bars = payload.get("bars")
    out: dict[str, list[dict[str, Any]]] = {}
    if isinstance(bars, dict):
        for sym, rows in bars.items():
            if isinstance(rows, list):
                out[_norm(str(sym))] = rows
    elif isinstance(bars, list) and payload.get("symbol"):
        out[_norm(str(payload["symbol"]))] = bars
    return out


def build_weekly_snapshot_from_daily(
    dailies: dict[str, pd.DataFrame],
    *,
    week_key: str,
    expected: list[str] | None = None,
    source: str = "alpaca_crypto_1d_30d",
) -> dict[str, Any]:
    adv: dict[str, Any] = {}
    rets: dict[str, pd.Series] = {}
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for sym, daily in (dailies or {}).items():
        ns = _norm(sym)
        seen.add(ns)
        med, why = median_adv_30d(daily)
        if med is None:
            rejected.append({"symbol": ns, "reason": why})
            continue
        adv[ns] = round(med, 2)
        rr = daily_log_returns(daily)
        if rr is not None:
            rets[ns] = rr
    for sym in expected or []:
        ns = _norm(sym)
        if ns in seen:
            continue
        rejected.append({"symbol": ns, "reason": "history_lt_30d"})
    corr = pairwise_corr(rets)
    corr_ser = {f"{a}|{b}": round(v, 4) for (a, b), v in corr.items()}
    return {
        "week_key": week_key,
        "min_daily_bars": MIN_DAILY_BARS,
        "adv_usd": adv,
        "corr": corr_ser,
        "rejected": rejected,
        "source": source,
        "note": (
            "1% ADV = mediana 30d del notional diario Alpaca; "
            "correlación de grupo usa 90d de velas 4h."
        ),
    }


def corr_from_4h(frames_4h: dict[str, Any] | None) -> dict[str, float]:
    rets: dict[str, pd.Series] = {}
    for sym, df in (frames_4h or {}).items():
        rr = log_returns_4h(df)
        if rr is not None:
            rets[_norm(sym)] = rr
    corr = pairwise_corr(rets)
    return {f"{a}|{b}": round(v, 4) for (a, b), v in corr.items()}


def build_weekly_snapshot(
    frames_4h: dict[str, pd.DataFrame],
    *,
    week_key: str,
    expected: list[str] | None = None,
) -> dict[str, Any]:
    """Fallback: resample 4h (or any DatetimeIndex OHLC) to daily, then same 30d rules."""
    dailies = {sym: daily_from_4h(df) for sym, df in (frames_4h or {}).items()}
    return build_weekly_snapshot_from_daily(
        dailies,
        week_key=week_key,
        expected=expected,
        source="4h_resampled_daily_30d",
    )


def adv_for_symbol(weekly: dict[str, Any] | None, symbol: str) -> tuple[float | None, str]:
    """Return (median ADV USD, reason). None → skip the name (fail closed)."""
    snap = weekly or {}
    ns = _norm(symbol)
    raw = (snap.get("adv_usd") or {}).get(ns)
    if raw is None:
        why = next(
            (r.get("reason") for r in (snap.get("rejected") or []) if r.get("symbol") == ns),
            "history_lt_30d",
        )
        return None, str(why or "history_lt_30d")
    try:
        adv = float(raw)
    except (TypeError, ValueError):
        return None, "history_lt_30d"
    if not np.isfinite(adv) or adv <= 0:
        return None, "adv_invalid"
    return adv, "ok"


async def fetch_alpaca_crypto_daily(
    symbols: list[str],
    *,
    now: datetime | None = None,
    http_get: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, pd.DataFrame]:
    """Daily crypto bars from Alpaca Market Data. Fail closed (empty) without keys/history."""
    wanted = [_norm(s) for s in symbols if s]
    if not wanted:
        return {}
    clock = now or datetime.now(timezone.utc)
    start = (clock - timedelta(days=ADV_LOOKBACK_DAYS + 10)).strftime("%Y-%m-%dT00:00:00Z")
    getter = http_get or _alpaca_data_get
    collected: dict[str, list[dict[str, Any]]] = {s: [] for s in wanted}
    page_token: str | None = None
    try:
        for _ in range(8):
            params: dict[str, Any] = {
                "symbols": ",".join(wanted),
                "timeframe": "1Day",
                "start": start,
                "limit": 1000,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            payload = await getter(ALPACA_CRYPTO_BARS_PATH, params)
            chunk = extract_alpaca_symbol_bars(payload if isinstance(payload, dict) else {})
            for sym, rows in chunk.items():
                collected.setdefault(sym, []).extend(rows)
            page_token = (payload or {}).get("next_page_token") if isinstance(payload, dict) else None
            if not page_token:
                break
    except Exception as exc:
        logger.warning("crypto_weekly.alpaca_daily_failed", error=str(exc))
        return {}
    out: dict[str, pd.DataFrame] = {}
    for sym, rows in collected.items():
        df = alpaca_bars_to_daily(rows)
        if df is not None and not df.empty:
            out[sym] = df
    return out


async def _alpaca_data_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    import httpx

    from config.settings import get_settings

    settings = get_settings()
    key = (settings.alpaca_beta_api_key or settings.alpaca_api_key or "").strip()
    secret = (settings.alpaca_beta_secret_key or settings.alpaca_secret_key or "").strip()
    if not key or not secret:
        raise RuntimeError("alpaca_data_keys_missing")
    base = (settings.alpaca_data_base_url or "https://data.alpaca.markets").rstrip("/")
    url = f"{base}{path}"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers, params=params)
        if not response.is_success:
            raise RuntimeError(f"alpaca_crypto_bars {response.status_code}: {response.text[:200]}")
        data = response.json()
        return data if isinstance(data, dict) else {}


def corr_pairs_from_snapshot(snap: dict[str, Any] | None) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for key, val in ((snap or {}).get("corr") or {}).items():
        if "|" not in str(key):
            continue
        a, b = str(key).split("|", 1)
        try:
            out[(a, b)] = float(val)
        except (TypeError, ValueError):
            continue
    return out
