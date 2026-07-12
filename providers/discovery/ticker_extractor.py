"""Extract and validate stock tickers from unstructured text."""

import re

_TICKER_CASH = re.compile(r"\$([A-Z]{1,5})\b")
_TICKER_BARE = re.compile(r"\b([A-Z]{2,5})\b")

_BLOCKLIST = frozenset({
    "CEO", "CFO", "COO", "CTO", "USA", "USD", "EUR", "GBP", "ETF", "ETFs",
    "IPO", "FDA", "SEC", "EPS", "GDP", "AI", "ML", "API", "NYSE", "NASDAQ",
    "AMEX", "OTC", "ATH", "ATL", "YTD", "QOQ", "YOY", "MOQ", "PE", "PB",
    "ROE", "ROI", "DCF", "MACD", "RSI", "EMA", "SMA", "IV", "OI", "DD",
    "WSB", "DDG", "LLM", "USD", "CNY", "JPY", "EUR", "GBP", "CAD", "AUD",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
    "WAS", "ONE", "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW",
    "ITS", "MAY", "NEW", "NOW", "OLD", "SEE", "TWO", "WAY", "WHO", "BOY",
    "DID", "LET", "PUT", "SAY", "SHE", "TOO", "USE", "BUY", "SELL", "HOLD",
    "LONG", "SHORT", "CALL", "PUTS", "CALLS", "MOON", "BEAR", "BULL", "NEWS",
    "FED", "CPI", "PPI", "FOMC", "GDP", "PMI", "ISM", "OPEC", "ECB", "BOJ",
    "SPY", "QQQ", "IWM", "DIA", "VIX", "TLT", "GLD", "SLV", "USO", "XLE",
    "XLF", "XLK", "XLV", "XLI", "XLP", "XLY", "XLB", "XLU", "XLRE", "ARKK",
})


def extract_tickers(text: str) -> list[str]:
    """Return unique uppercase tickers found in *text*, ordered by first appearance."""
    if not text:
        return []

    seen: set[str] = set()
    ordered: list[str] = []

    for match in _TICKER_CASH.finditer(text):
        ticker = match.group(1)
        if ticker not in _BLOCKLIST and ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)

    for match in _TICKER_BARE.finditer(text):
        ticker = match.group(1)
        if ticker not in _BLOCKLIST and ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)

    return ordered
