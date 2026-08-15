"""Crypto tradeable universe for multi-asset beta (Alpaca USD pairs)."""

from __future__ import annotations

from domain.multiasset import DeskUniverseItem
from utils.logging import get_logger

logger = get_logger(__name__)

# Stablecoins / cash parking — never treat as discretionary buys
_STABLE_BASES = frozenset({"USDT", "USDC", "USDG", "DAI", "USD"})

# Fallback USD spot pairs supported on Alpaca (expand as Alpaca adds coins)
_DEFAULT_USD_CRYPTOS: list[tuple[str, str]] = [
    ("BTC", "Bitcoin"),
    ("ETH", "Ethereum"),
    ("SOL", "Solana"),
    ("XRP", "XRP"),
    ("ADA", "Cardano"),
    ("AVAX", "Avalanche"),
    ("DOGE", "Dogecoin"),
    ("DOT", "Polkadot"),
    ("LINK", "Chainlink"),
    ("LTC", "Litecoin"),
    ("BCH", "Bitcoin Cash"),
    ("UNI", "Uniswap"),
    ("AAVE", "Aave"),
    ("ATOM", "Cosmos"),  # may 404 on some accounts — filtered by Alpaca live list
    ("NEAR", "NEAR"),
    ("ARB", "Arbitrum"),
    ("OP", "Optimism"),
    ("FIL", "Filecoin"),
    ("GRT", "The Graph"),
    ("CRV", "Curve"),
    ("MKR", "Maker"),
    ("SUSHI", "SushiSwap"),
    ("BAT", "Basic Attention"),
    ("XTZ", "Tezos"),
    ("YFI", "Yearn"),
    ("SHIB", "Shiba Inu"),
    ("PEPE", "Pepe"),
    ("BONK", "Bonk"),
    ("WIF", "dogwifhat"),
    ("RENDER", "Render"),
    ("POL", "Polygon"),
    ("ONDO", "Ondo"),
    ("LDO", "Lido"),
    ("PAXG", "PAX Gold"),
    ("TRUMP", "TRUMP"),
    ("HYPE", "Hyperliquid"),
    ("SKY", "Sky"),
]


def _item(base: str, label: str) -> DeskUniverseItem:
    return DeskUniverseItem(
        symbol=f"{base}/USD",
        label=label or base,
        asset_class="crypto",
        notes="spot USD",
    )


def default_crypto_universe() -> list[DeskUniverseItem]:
    return [_item(b, name) for b, name in _DEFAULT_USD_CRYPTOS]


def _normalize_alpaca_symbol(sym: str) -> str | None:
    s = (sym or "").strip().upper().replace(" ", "")
    if not s:
        return None
    if "/" in s:
        base, quote = s.split("/", 1)
    elif s.endswith("USD") and len(s) > 3:
        base, quote = s[:-3], "USD"
    else:
        return None
    if quote != "USD":
        return None
    if base in _STABLE_BASES:
        return None
    return f"{base}/USD"


async def resolve_crypto_universe(broker=None) -> list[DeskUniverseItem]:
    """Prefer live Alpaca crypto assets; fall back to curated USD list."""
    fallback = default_crypto_universe()
    by_sym = {i.symbol: i for i in fallback}

    if broker is None or not getattr(broker, "is_configured", lambda: False)():
        return list(by_sym.values())

    try:
        assets = await broker.list_crypto_assets()
    except Exception as exc:
        logger.warning("crypto.universe.alpaca_failed", error=str(exc))
        return list(by_sym.values())

    live: list[DeskUniverseItem] = []
    for a in assets or []:
        if not isinstance(a, dict):
            continue
        if a.get("tradable") is False or a.get("status") not in (None, "active"):
            # still include if tradable true
            if not a.get("tradable"):
                continue
        sym = _normalize_alpaca_symbol(str(a.get("symbol") or ""))
        if not sym:
            continue
        base = sym.split("/")[0]
        label = by_sym.get(sym).label if sym in by_sym else base
        live.append(
            DeskUniverseItem(
                symbol=sym,
                label=label,
                asset_class="crypto",
                notes="alpaca spot",
            )
        )

    if not live:
        return list(by_sym.values())

    # Stable order: majors first, then alpha
    majors = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD", "ADA/USD", "AVAX/USD", "LINK/USD"]
    rank = {s: i for i, s in enumerate(majors)}
    live.sort(key=lambda x: (rank.get(x.symbol, 100), x.symbol))
    logger.info("crypto.universe.resolved", count=len(live), source="alpaca")
    return live
