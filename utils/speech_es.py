"""Spanish spoken forms for TTS — avoid English digit reading and rushed %.

Examples:
  +14%  → crecimiento del catorce por ciento
  -0.5% → decrecimiento del cero punto cinco por ciento
  $21.68 → veintiún dólares con sesenta y ocho centavos (approx)
"""

from __future__ import annotations

import re

_UNITS = (
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
)
_TENS = (
    "",
    "",
    "veinte",
    "treinta",
    "cuarenta",
    "cincuenta",
    "sesenta",
    "setenta",
    "ochenta",
    "noventa",
)
_HUNDREDS = (
    "",
    "ciento",
    "doscientos",
    "trescientos",
    "cuatrocientos",
    "quinientos",
    "seiscientos",
    "setecientos",
    "ochocientos",
    "novecientos",
)


def _under_100(n: int, *, feminine: bool = False) -> str:
    if n < 20:
        if feminine and n == 1:
            return "una"
        return _UNITS[n]
    if n == 20:
        return "veinte"
    if 21 <= n <= 29:
        special = {
            21: "veintiuno",
            22: "veintidós",
            23: "veintitrés",
            24: "veinticuatro",
            25: "veinticinco",
            26: "veintiséis",
            27: "veintisiete",
            28: "veintiocho",
            29: "veintinueve",
        }
        if feminine and n == 21:
            return "veintiuna"
        # Before "mil/dólares" prefer veintiún
        return special[n]
    ten, unit = divmod(n, 10)
    if unit == 0:
        return _TENS[ten]
    u = "una" if feminine and unit == 1 else _UNITS[unit]
    return f"{_TENS[ten]} y {u}"


def _under_1000(n: int, *, feminine: bool = False) -> str:
    if n < 100:
        return _under_100(n, feminine=feminine)
    if n == 100:
        return "cien"
    h, rest = divmod(n, 100)
    head = _HUNDREDS[h]
    if rest == 0:
        return head
    return f"{head} {_under_100(rest, feminine=feminine)}"


def integer_to_es(n: int, *, feminine: bool = False) -> str:
    n = int(n)
    if n < 0:
        return "menos " + integer_to_es(-n, feminine=feminine)
    if n < 1000:
        return _under_1000(n, feminine=feminine)
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        if thousands == 1:
            head = "mil"
        else:
            th = integer_to_es(thousands)
            th = th.replace("veintiuno", "veintiún").replace("uno", "un")
            head = f"{th} mil"
        if rest == 0:
            return head
        return f"{head} {_under_1000(rest, feminine=feminine)}"
    # Fallback for large numbers — digit by digit is clearer than English TTS
    return " ".join(_UNITS[int(d)] if d.isdigit() else d for d in str(n))


def decimal_to_es(value: float, *, max_decimals: int = 2) -> str:
    """Spoken decimal: 0.5 → cero punto cinco; 14 → catorce; 2.9 → dos punto nueve."""
    sign = ""
    v = float(value)
    if v < 0:
        sign = "menos "
        v = abs(v)
    # Normalize commas
    text = f"{v:.{max_decimals}f}".rstrip("0").rstrip(".")
    if "." not in text:
        return sign + integer_to_es(int(text))
    whole_s, frac_s = text.split(".", 1)
    whole = integer_to_es(int(whole_s))
    # Read fractional digits individually for clarity (0.05 → cero punto cero cinco)
    frac_parts = [_UNITS[int(ch)] for ch in frac_s if ch.isdigit()]
    if not frac_parts:
        return sign + whole
    return f"{sign}{whole} punto {' '.join(frac_parts)}"


def percent_to_es(value: float, *, signed: bool = True) -> str:
    """+14 → crecimiento del catorce por ciento; -0.5 → decrecimiento del cero punto cinco…"""
    v = float(value)
    magnitude = decimal_to_es(abs(v))
    if not signed or abs(v) < 1e-12:
        return f"{magnitude} por ciento"
    if v > 0:
        return f"crecimiento del {magnitude} por ciento"
    return f"decrecimiento del {magnitude} por ciento"


def money_usd_to_es(value: float) -> str:
    v = float(value)
    sign = "menos " if v < 0 else ""
    v = abs(v)
    dollars = int(v)
    cents = int(round((v - dollars) * 100))
    if cents == 100:
        dollars += 1
        cents = 0
    d_word = integer_to_es(dollars)
    if dollars == 1:
        d_phrase = "un dólar"
    else:
        d_phrase = f"{d_word} dólares"
    if cents == 0:
        return f"{sign}{d_phrase}"
    c_word = integer_to_es(cents)
    c_phrase = "un centavo" if cents == 1 else f"{c_word} centavos"
    return f"{sign}{d_phrase} con {c_phrase}"


_RE_PCT_SYMBOL = re.compile(
    r"(?<![A-Za-z0-9_])([+-])?\s*(\d+(?:[.,]\d+)?)\s*%",
    re.UNICODE,
)
_RE_PCT_WORDS = re.compile(
    r"(?<![A-Za-z0-9_])([+-])?\s*(\d+(?:[.,]\d+)?)\s*por\s+ciento\b",
    re.IGNORECASE | re.UNICODE,
)
_RE_MONEY = re.compile(
    r"(?<![A-Za-z0-9_])\$\s*(\d+(?:[.,]\d+)?)",
    re.UNICODE,
)
_RE_SIGNED_PLAIN = re.compile(
    r"(?<![A-Za-z0-9_./])([+-])(\d+(?:[.,]\d+)?)(?!\s*(?:%|por\s+ciento|dólar|dolares|dólares))",
    re.IGNORECASE | re.UNICODE,
)


def _parse_num(raw: str) -> float:
    return float(raw.replace(",", "."))


def rewrite_for_speech(text: str) -> str:
    """Rewrite percentages/money/signed numbers into clear Spanish for TTS."""
    if not text:
        return ""
    s = str(text)

    def _pct(m: re.Match[str]) -> str:
        sign = m.group(1) or ""
        val = _parse_num(m.group(2))
        if sign == "+":
            val = abs(val)
            return percent_to_es(val, signed=True)
        if sign == "-":
            return percent_to_es(-abs(val), signed=True)
        # No explicit sign — keep neutral wording
        return percent_to_es(val, signed=False)

    s = _RE_PCT_SYMBOL.sub(_pct, s)
    s = _RE_PCT_WORDS.sub(_pct, s)

    def _money(m: re.Match[str]) -> str:
        return money_usd_to_es(_parse_num(m.group(1)))

    s = _RE_MONEY.sub(_money, s)

    def _signed(m: re.Match[str]) -> str:
        sign, num = m.group(1), m.group(2)
        val = _parse_num(num)
        spoken = decimal_to_es(val)
        if sign == "+":
            return f"más {spoken}"
        return f"menos {spoken}"

    s = _RE_SIGNED_PLAIN.sub(_signed, s)

    # Soft pauses so TTS doesn't rush number phrases
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace(" por ciento", " por ciento.")
    s = re.sub(r"\.\.+", ".", s)
    s = re.sub(r"\.\s*\.", ".", s)
    return s
