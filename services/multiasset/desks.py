"""Desk catalogs and strategy defaults for multi-asset beta."""

from __future__ import annotations

from domain.multiasset import AssetDeskId, DeskStrategy, DeskUniverseItem
from services.multiasset.crypto_universe import default_crypto_universe

_GOLD = DeskStrategy(
    desk="gold",
    name="Mesa Oro · Paper",
    thesis=(
        "Especialista de tendencia (Donchian 20 + SMA50 + filtro DXY) en ETFs GLD/IAU/GLDM. "
        "Riesgo 2.5%/trade, stop ATR 2.5× (1R), trail 3×. Paper 1x, sin futuros ni margen."
    ),
    horizon="swing 3–15 días",
    max_notional_usd=300.0,
    default_stop_pct=0.04,
    default_target_pct=0.08,
    allow_fractional=True,
    time_in_force="day",
    symbols=[
        DeskUniverseItem(symbol="GLD", label="SPDR Gold Shares", asset_class="us_equity", notes="proxy oro #1"),
        DeskUniverseItem(symbol="IAU", label="iShares Gold Trust", asset_class="us_equity", notes="coste bajo"),
        DeskUniverseItem(symbol="GLDM", label="SPDR Gold MiniShares", asset_class="us_equity", notes="ticket menor"),
    ],
    agent_names=[
        "gold_trend_specialist",
        "gold_macro_agent",
        "gold_technical_agent",
        "gold_flow_agent",
    ],
    disclaimer="Beta paper. Oro vía ETF; no opera futuros COMEX.",
)

_FOREX = DeskStrategy(
    desk="forex",
    name="Mesa Forex · Paper (proxies ETF)",
    thesis=(
        "Alpaca no opera FX spot ni CFD. Especialista TSMOM + ADX sobre proxies ETF "
        "UUP/FXE/FXB/FXY. Riesgo 2.5%/trade, stop ATR 2.0×, trail 2.5×. Paper 1x."
    ),
    horizon="swing 2–10 días",
    max_notional_usd=250.0,
    default_stop_pct=0.035,
    default_target_pct=0.07,
    allow_fractional=True,
    time_in_force="day",
    symbols=[
        DeskUniverseItem(symbol="UUP", label="Invesco DB USD Index Bullish", asset_class="us_equity", notes="proxy USD"),
        DeskUniverseItem(symbol="FXE", label="Invesco CurrencyShares Euro", asset_class="us_equity", notes="proxy EUR"),
        DeskUniverseItem(symbol="FXB", label="Invesco CurrencyShares Pound", asset_class="us_equity", notes="proxy GBP"),
        DeskUniverseItem(symbol="FXY", label="Invesco CurrencyShares Yen", asset_class="us_equity", notes="proxy JPY"),
    ],
    agent_names=[
        "fx_momentum_specialist",
        "fx_macro_agent",
        "fx_technical_agent",
        "fx_risk_agent",
    ],
    disclaimer="Beta paper. Proxies ETF — no spot FX ni apalancamiento CFD.",
)

_CRYPTO = DeskStrategy(
    desk="crypto",
    name="Mesa Crypto · Paper",
    thesis=(
        "Estrategia A combo #9: Donchian histéresis L=120/240/480 en velas 4h, S=promedio. "
        "Régimen BTC close > SMA 1.200 velas 4h (200d) para todas las monedas. "
        "Peso S×min(1, 25%/vol 30d)×tope. Chandelier 8×ATR14 al cierre; fill apertura "
        "siguiente (software). Sin TP. Alpaca crypto no acepta stop/bracket; paper no "
        "modela bien gaps. Gate = JSON de elegibilidad; no se recalcula OOS. Paper 1x."
    ),
    horizon="swing 4h · Donchian 120/240/480",
    max_notional_usd=5_000.0,
    default_stop_pct=0.08,
    default_target_pct=0.16,
    allow_fractional=True,
    time_in_force="gtc",
    symbols=default_crypto_universe(),
    agent_names=[
        "crypto_strategy_a",
    ],
    disclaimer="Beta paper. Estrategia A + límites Riesgo. LIVE no se toca.",
)

DESKS: dict[AssetDeskId, DeskStrategy] = {
    "gold": _GOLD,
    "forex": _FOREX,
    "crypto": _CRYPTO,
}


def get_desk(desk: AssetDeskId) -> DeskStrategy:
    if desk not in DESKS:
        raise KeyError(f"Desk desconocido: {desk}")
    return DESKS[desk]


def set_crypto_symbols(symbols: list[DeskUniverseItem]) -> DeskStrategy:
    """Replace crypto desk universe (e.g. after Alpaca asset sync)."""
    base = DESKS["crypto"]
    updated = base.model_copy(update={"symbols": symbols})
    DESKS["crypto"] = updated
    return updated


def desk_symbols(desk: AssetDeskId) -> set[str]:
    return {s.symbol.upper().replace(" ", "") for s in get_desk(desk).symbols}


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if "/" in s:
        return s
    if s.endswith("USD") and len(s) > 3 and s not in {"GLDM"}:
        base = s[:-3]
        if base.isalpha() and len(base) <= 10:
            return f"{base}/USD"
    return s


def same_symbol(left: str | None, right: str | None) -> bool:
    """True when BTCUSD and BTC/USD (or ETHUSD / ETH/USD) are the same pair."""
    if not left or not right:
        return False
    return normalize_symbol(str(left)) == normalize_symbol(str(right))
