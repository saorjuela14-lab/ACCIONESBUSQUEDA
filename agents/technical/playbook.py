"""Strategy playbook: map structure + confluence + volume into an actionable opinion."""

from __future__ import annotations

from typing import Any

from utils.narrative_es import bias_label


def build_playbook(
    *,
    ticker: str,
    price: float,
    daily: dict[str, Any],
    structure: dict[str, Any],
    confluence: dict[str, Any],
    volume: dict[str, Any],
    historical: dict[str, Any],
    trade_levels: dict[str, Any],
    unfilled_gaps: int = 0,
    committee_notes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Produce a Spanish playbook aligned with common 2025–2026 TA frameworks:
    location → direction → confirmation → risk (ChartMini / multi-TF top-down).
    """
    struct = structure.get("structure") or "range"
    htf = confluence.get("htf_bias") or "neutral"
    ltf = confluence.get("ltf_bias") or "neutral"
    aligned = bool(confluence.get("aligned_with_htf"))
    vol_confirm = volume.get("volume_confirm") or "unknown"
    rsi = daily.get("rsi")
    adx = daily.get("adx")
    rr = trade_levels.get("risk_reward_ratio")

    # Strategy selection
    if struct == "uptrend" and htf == "bullish" and aligned:
        strategy = "swing_pullback"
        strategy_es = "Swing alcista (pullback en tendencia)"
        thesis = (
            f"{ticker}: sesgo HTF alcista con LTF alineado. Buscar compras en retrocesos "
            f"hacia soporte/EMA, no chasear rupturas extendidas."
        )
    elif struct == "downtrend" and htf == "bearish" and aligned:
        strategy = "swing_rally_short"
        strategy_es = "Swing bajista (rally en tendencia bajista)"
        thesis = (
            f"{ticker}: estructura y HTF bajistas. Preferir ventas en rebotes a resistencia "
            f"o mantenerse fuera si no se opera short."
        )
    elif struct == "range" or confluence.get("label") == "mixed":
        strategy = "mean_reversion"
        strategy_es = "Mean reversion en rango"
        thesis = (
            f"{ticker}: sin tendencia clara — operar extremos (soporte/resistencia) "
            f"con objetivos hacia la media, stops cortos fuera del rango."
        )
    elif htf == "bullish" and (rsi is not None and rsi < 35):
        strategy = "momentum_dip"
        strategy_es = "Momentum dip-buy"
        thesis = (
            f"{ticker}: HTF alcista con RSI diario deprimido — zona típica de acumulación "
            f"si el volumen confirma y no se rompe estructura."
        )
    elif htf == "bearish" and (rsi is not None and rsi > 65):
        strategy = "momentum_fade"
        strategy_es = "Fade de sobrecompra en sesgo bajista"
        thesis = (
            f"{ticker}: HTF bajista con RSI elevado — malo para perseguir largos; "
            f"esperar rechazo en resistencia."
        )
    else:
        strategy = "wait"
        strategy_es = "Esperar confluencia"
        thesis = (
            f"{ticker}: señales mixtas entre estructura ({structure.get('label_es')}) "
            f"y confluencia ({confluence.get('label_es')}). Mejor no forzar entrada."
        )

    checklist: list[str] = [
        f"Estructura: {structure.get('label_es')} (conf. {structure.get('confidence', 0):.0%})",
        f"Confluencia multi-TF: {confluence.get('label_es')} "
        f"(acuerdo {confluence.get('agreement_pct', 0)}%, HTF {bias_label(htf)} / LTF {bias_label(ltf)})",
        f"Volumen: {volume.get('volume_confirm_es')}"
        + (f" · ratio {volume.get('volume_ratio')}x" if volume.get("volume_ratio") is not None else ""),
    ]
    if volume.get("above_vwap") is not None:
        checklist.append(
            "Precio sobre VWAP (sesgo intradía alcista)"
            if volume["above_vwap"]
            else "Precio bajo VWAP (sesgo intradía bajista)"
        )
    if adx is not None:
        strength = "tendencia fuerte" if adx >= 25 else "tendencia débil / rango probable"
        checklist.append(f"ADX {adx:.1f} → {strength}")
    if rsi is not None:
        zone = "sobreventa" if rsi < 30 else "sobrecompra" if rsi > 70 else "neutral"
        checklist.append(f"RSI diario {rsi:.1f} ({zone})")
    if unfilled_gaps:
        checklist.append(f"Gaps sin cubrir: {unfilled_gaps} (el mercado suele buscar fill)")
    if rr:
        checklist.append(f"R/R estimado {rr}x (objetivo ≥1.5–2x es filtro profesional)")
    if committee_notes:
        checklist.append("Comité: " + "; ".join(committee_notes[:2]))

    # Invalidation / risk
    invalidation = []
    if struct == "uptrend" and structure.get("last_low"):
        invalidation.append(f"Cierre bajo último swing low ~${structure['last_low']}")
    if struct == "downtrend" and structure.get("last_high"):
        invalidation.append(f"Cierre sobre último swing high ~${structure['last_high']}")
    if trade_levels.get("stop_loss") is not None:
        invalidation.append(f"Stop técnico ${trade_levels['stop_loss']}")

    # Historical edge blurb
    hist_lines: list[str] = []
    if historical.get("available") and historical.get("best"):
        best = historical["best"]
        hist_lines.append(
            f"Edge histórico local ({best.get('label_es')}): "
            f"acierto {best.get('hit_rate')}% en {best.get('samples')} señales "
            f"(horizonte {best.get('horizon_bars')} barras, ret. medio {best.get('avg_forward_return_pct')}%)."
        )
    elif historical.get("note"):
        hist_lines.append(str(historical["note"]))

    opinion = "neutral"
    if strategy in ("swing_pullback", "momentum_dip"):
        opinion = "constructive"
    elif strategy in ("swing_rally_short", "momentum_fade"):
        opinion = "defensive"
    elif strategy == "mean_reversion":
        opinion = "tactical"
    elif strategy == "wait":
        opinion = "stand_aside"

    opinion_es = {
        "constructive": "Constructiva (sesgo largo con reglas)",
        "defensive": "Defensiva / cautela en largos",
        "tactical": "Táctica de rango",
        "stand_aside": "Mejor al margen hasta confluencia",
        "neutral": "Neutral",
    }[opinion]

    summary = " ".join(
        [
            f"Playbook {strategy_es} para {ticker} @ ${price:.2f}.",
            thesis,
            f"Opinión: {opinion_es}.",
        ]
        + hist_lines[:1]
    )

    return {
        "strategy": strategy,
        "strategy_es": strategy_es,
        "opinion": opinion,
        "opinion_es": opinion_es,
        "thesis": thesis,
        "checklist": checklist,
        "invalidation": invalidation,
        "historical_note": hist_lines[0] if hist_lines else None,
        "summary": summary,
        "framework": "top-down multi-TF + estructura + volumen + R/R (práctica 2025–2026)",
    }
