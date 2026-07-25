"""Multi-timeframe confluence scoring (top-down HTF bias)."""

from __future__ import annotations

from typing import Any

# Higher weight on daily/weekly for directional bias (industry standard top-down).
TF_WEIGHTS: dict[str, float] = {
    "5m": 0.4,
    "15m": 0.5,
    "30m": 0.6,
    "1H": 0.8,
    "4H": 1.1,
    "1D": 1.6,
    "1W": 1.8,
    "1M": 1.4,
}

HTF = ("4H", "1D", "1W", "1M")
LTF = ("5m", "15m", "30m", "1H")


def score_confluence(timeframe_results: dict[str, dict]) -> dict[str, Any]:
    """Aggregate TF biases into a confluence readout."""
    biases: dict[str, str] = {}
    weighted = 0.0
    weight_sum = 0.0
    bullish: list[str] = []
    bearish: list[str] = []
    neutral: list[str] = []

    for tf, data in timeframe_results.items():
        if not data:
            continue
        bias = data.get("bias") or "neutral"
        biases[tf] = bias
        w = TF_WEIGHTS.get(tf, 1.0)
        signed = 1.0 if bias == "bullish" else -1.0 if bias == "bearish" else 0.0
        # Blend discrete bias with continuous TF score when present
        raw = float(data.get("score") or 0.0)
        signed = signed * 0.6 + (1.0 if raw > 0 else -1.0 if raw < 0 else 0.0) * 0.4
        weighted += signed * w
        weight_sum += w
        if bias == "bullish":
            bullish.append(tf)
        elif bias == "bearish":
            bearish.append(tf)
        else:
            neutral.append(tf)

    if weight_sum <= 0:
        return {
            "score": 0.0,
            "label": "sin_datos",
            "label_es": "sin datos suficientes",
            "agreement_pct": 0.0,
            "biases": {},
            "aligned": [],
            "conflict": [],
            "htf_bias": "neutral",
            "ltf_bias": "neutral",
            "aligned_with_htf": False,
        }

    norm = weighted / weight_sum  # -1..1
    score = round(norm * 100, 1)

    majority = max(len(bullish), len(bearish), len(neutral))
    total = max(len(bullish) + len(bearish) + len(neutral), 1)
    agreement_pct = round(100.0 * majority / total, 1)

    if score >= 35:
        label, label_es = "strong_bullish", "confluencia alcista fuerte"
    elif score >= 12:
        label, label_es = "bullish", "confluencia alcista"
    elif score <= -35:
        label, label_es = "strong_bearish", "confluencia bajista fuerte"
    elif score <= -12:
        label, label_es = "bearish", "confluencia bajista"
    else:
        label, label_es = "mixed", "señales mixtas / sin confluencia clara"

    def _dom(tfs: tuple[str, ...]) -> str:
        vals = [biases[t] for t in tfs if t in biases]
        if not vals:
            return "neutral"
        b = sum(1 for v in vals if v == "bullish")
        s = sum(1 for v in vals if v == "bearish")
        if b > s:
            return "bullish"
        if s > b:
            return "bearish"
        return "neutral"

    htf_bias = _dom(HTF)
    ltf_bias = _dom(LTF)
    aligned_with_htf = htf_bias != "neutral" and ltf_bias == htf_bias

    conflict = []
    if htf_bias != "neutral" and ltf_bias != "neutral" and htf_bias != ltf_bias:
        conflict = [f"HTF {htf_bias} vs LTF {ltf_bias}"]

    aligned = bullish if htf_bias == "bullish" else bearish if htf_bias == "bearish" else []

    return {
        "score": score,
        "label": label,
        "label_es": label_es,
        "agreement_pct": agreement_pct,
        "biases": biases,
        "aligned": aligned,
        "conflict": conflict,
        "htf_bias": htf_bias,
        "ltf_bias": ltf_bias,
        "aligned_with_htf": aligned_with_htf,
        "bullish_tfs": bullish,
        "bearish_tfs": bearish,
        "neutral_tfs": neutral,
    }
