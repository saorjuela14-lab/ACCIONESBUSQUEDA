"""Strategy playbook: map structure + confluence + volume into an actionable opinion."""

from __future__ import annotations

from typing import Any

from agents.technical.market_opinion import tech_vs_market_alignment
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
    market_opinion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Produce a Spanish playbook aligned with common 2025–2026 TA frameworks:
    location → direction → confirmation → risk (ChartMini / multi-TF top-down),
    plus live market opinion (news/social/retail) when available.
    """
    struct = structure.get("structure") or "range"
    htf = confluence.get("htf_bias") or "neutral"
    ltf = confluence.get("ltf_bias") or "neutral"
    aligned = bool(confluence.get("aligned_with_htf"))
    rsi = daily.get("rsi")
    adx = daily.get("adx")
    rr = trade_levels.get("risk_reward_ratio")
    mkt = market_opinion if market_opinion and market_opinion.get("available") else None

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

    # Market opinion can upgrade/downgrade strategy framing
    alignment = None
    if mkt:
        alignment = tech_vs_market_alignment(htf if htf != "neutral" else (daily.get("bias") or "neutral"), mkt.get("label") or "neutral")
        if alignment["status"] == "aligned" and mkt["label"] == "bullish" and strategy == "wait":
            strategy, strategy_es = "momentum_dip", "Sesgo largo táctico (narrativa a favor)"
            thesis = (
                f"{ticker}: técnico mixto pero opinión de mercado alcista "
                f"({mkt.get('aggregated_score', 0):+.1f}). Buscar entradas con R/R claro."
            )
        elif alignment["status"] == "diverged":
            thesis += f" Atención: {alignment['note']}"
            if strategy in ("swing_pullback", "momentum_dip") and mkt["label"] == "bearish":
                strategy, strategy_es = "wait", "Esperar (técnico vs mercado divergente)"
                thesis = (
                    f"{ticker}: setup técnico constructivo pero opinión de mercado bajista "
                    f"({mkt.get('aggregated_score', 0):+.1f}). Mejor esperar o reducir riesgo."
                )
            elif strategy in ("swing_rally_short", "momentum_fade") and mkt["label"] == "bullish":
                strategy, strategy_es = "wait", "Esperar (técnico vs mercado divergente)"
                thesis = (
                    f"{ticker}: técnico defensivo pero narrativa de mercado alcista "
                    f"({mkt.get('aggregated_score', 0):+.1f}). No forzar shorts agresivos."
                )
        elif alignment["status"] == "aligned" and mkt["label"] == "bullish" and strategy in ("swing_pullback", "momentum_dip"):
            thesis += (
                f" Opinión de mercado alcista ({mkt.get('aggregated_score', 0):+.1f}) "
                "refuerza el sesgo largo con reglas."
            )
        elif alignment["status"] == "aligned" and mkt["label"] == "bearish" and strategy in ("swing_rally_short", "momentum_fade", "wait"):
            thesis += (
                f" Opinión de mercado bajista ({mkt.get('aggregated_score', 0):+.1f}) "
                "refuerza la cautela."
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
    if mkt:
        ch = mkt.get("channels") or {}
        bits = []
        for key, label in (("news", "Noticias"), ("social", "Social"), ("retail", "Minorista"), ("analyst", "Analistas")):
            sc = (ch.get(key) or {}).get("score")
            if sc is not None:
                bits.append(f"{label} {float(sc):+.0f}")
        checklist.append(
            f"Opinión mercado: {mkt.get('label_es')} ({mkt.get('aggregated_score', 0):+.1f})"
            + (f" · {' · '.join(bits[:4])}" if bits else "")
        )
        if alignment:
            checklist.append(f"Técnico vs mercado: {alignment['status_es']} — {alignment['note']}")
        for factor in (mkt.get("top_factors") or [])[:2]:
            checklist.append(f"Factor: {factor}")
    if committee_notes:
        checklist.append("Comité: " + "; ".join(committee_notes[:2]))

    invalidation = []
    if struct == "uptrend" and structure.get("last_low"):
        invalidation.append(f"Cierre bajo último swing low ~${structure['last_low']}")
    if struct == "downtrend" and structure.get("last_high"):
        invalidation.append(f"Cierre sobre último swing high ~${structure['last_high']}")
    if trade_levels.get("stop_loss") is not None:
        invalidation.append(f"Stop técnico ${trade_levels['stop_loss']}")
    if mkt and mkt.get("label") == "bullish" and strategy in ("swing_pullback", "momentum_dip"):
        invalidation.append("Si sentimiento agregado cae fuerte (< -15) sin precio confirmar, recortar")

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

    # Soften constructive if market diverged
    if mkt and alignment and alignment["status"] == "diverged" and opinion == "constructive":
        opinion = "stand_aside"

    opinion_es = {
        "constructive": "Constructiva (sesgo largo con reglas)",
        "defensive": "Defensiva / cautela en largos",
        "tactical": "Táctica de rango",
        "stand_aside": "Mejor al margen hasta confluencia",
        "neutral": "Neutral",
    }[opinion]

    summary_parts = [
        f"Playbook {strategy_es} para {ticker} @ ${price:.2f}.",
        thesis,
        f"Opinión: {opinion_es}.",
    ]
    if mkt:
        summary_parts.append(mkt.get("headline") or f"Mercado {_label_es_safe(mkt.get('label'))}.")
        if mkt.get("summary"):
            summary_parts.append(str(mkt["summary"])[:180])
    summary_parts.extend(hist_lines[:1])

    out = {
        "strategy": strategy,
        "strategy_es": strategy_es,
        "opinion": opinion,
        "opinion_es": opinion_es,
        "thesis": thesis,
        "checklist": checklist,
        "invalidation": invalidation,
        "historical_note": hist_lines[0] if hist_lines else None,
        "summary": " ".join(summary_parts),
        "framework": (
            "top-down multi-TF + estructura + volumen + R/R + opinión de mercado "
            "(noticias/social/minorista/analistas)"
        ),
        "market_opinion": mkt,
        "tech_market_alignment": alignment,
    }
    return out


def _label_es_safe(label: str | None) -> str:
    return {"bullish": "alcista", "bearish": "bajista", "neutral": "neutral"}.get(label or "", "neutral")
