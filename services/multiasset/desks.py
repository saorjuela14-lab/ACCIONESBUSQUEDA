"""Desk catalogs and strategy defaults for multi-asset beta."""

from __future__ import annotations

from domain.multiasset import AssetDeskId, DeskStrategy, DeskUniverseItem

_GOLD = DeskStrategy(
    desk="gold",
    name="Mesa Oro · Paper",
    thesis=(
        "Sesgo a oro físico vía ETFs (GLD/IAU): cobertura macro, dólar real, "
        "aversión al riesgo y flujo de ETFs. Sin futuros GC."
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
    agent_names=["gold_macro_agent", "gold_technical_agent", "gold_flow_agent"],
    disclaimer="Beta paper. Oro vía ETF; no opera futuros COMEX.",
)

_FOREX = DeskStrategy(
    desk="forex",
    name="Mesa Forex · Paper (proxies ETF)",
    thesis=(
        "Alpaca no opera CFD de FX. Esta mesa especializa agentes en diferenciales "
        "de tipos/riesgo y opera proxies líquidos: UUP (USD), FXE (EUR), FXB (GBP), FXY (JPY)."
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
    agent_names=["fx_macro_agent", "fx_technical_agent", "fx_risk_agent"],
    disclaimer="Beta paper. Proxies ETF — no spot FX ni apalancamiento CFD.",
)

_CRYPTO = DeskStrategy(
    desk="crypto",
    name="Mesa Crypto · Paper",
    thesis=(
        "Momentum y riesgo en spot crypto 24/7 (BTC/ETH/SOL). "
        "TIF GTC, fracciones permitidas, sin brackets day de equity."
    ),
    horizon="intradía–swing 1–7 días",
    max_notional_usd=200.0,
    default_stop_pct=0.08,
    default_target_pct=0.16,
    allow_fractional=True,
    time_in_force="gtc",
    symbols=[
        DeskUniverseItem(symbol="BTC/USD", label="Bitcoin", asset_class="crypto", notes="spot"),
        DeskUniverseItem(symbol="ETH/USD", label="Ethereum", asset_class="crypto", notes="spot"),
        DeskUniverseItem(symbol="SOL/USD", label="Solana", asset_class="crypto", notes="spot"),
    ],
    agent_names=["crypto_momentum_agent", "crypto_sentiment_agent", "crypto_risk_agent"],
    disclaimer="Beta paper. Crypto spot Alpaca; alta volatilidad.",
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


def desk_symbols(desk: AssetDeskId) -> set[str]:
    return {s.symbol.upper().replace(" ", "") for s in get_desk(desk).symbols}


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    # Alpaca crypto accepts BTC/USD or BTCUSD
    if "/" in s:
        return s
    return s
