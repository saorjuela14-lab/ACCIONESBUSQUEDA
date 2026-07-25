"""Live market opinion for technical playbooks (news / social / retail / analysts)."""

from __future__ import annotations

from typing import Any

from agents.technical.context import PriorContext


def _label_from_score(score: float | None, *, bull: float = 10.0, bear: float = -10.0) -> str:
    if score is None:
        return "neutral"
    if score >= bull:
        return "bullish"
    if score <= bear:
        return "bearish"
    return "neutral"


def _label_es(label: str) -> str:
    return {
        "bullish": "alcista",
        "bearish": "bajista",
        "neutral": "neutral",
        "mixed": "mixta",
    }.get(label, label)


def build_market_opinion_from_engine(report: Any) -> dict[str, Any]:
    """Normalize SentimentEngineReport (model or dict) into playbook market_opinion."""
    if report is None:
        return {"available": False}

    if hasattr(report, "model_dump"):
        data = report.model_dump(mode="json")
    elif isinstance(report, dict):
        data = report
    else:
        return {"available": False}

    score = float(data.get("aggregated_score") or 0.0)
    label = data.get("aggregated_label") or _label_from_score(score)
    channels = {}
    for key in ("institutional", "retail", "social", "news", "analyst"):
        ch = data.get(key) or {}
        if isinstance(ch, dict):
            channels[key] = {
                "score": ch.get("score"),
                "confidence": ch.get("confidence"),
                "trend": ch.get("trend"),
                "sample_size": ch.get("sample_size") or 0,
                "top_factors": (ch.get("top_factors") or [])[:3],
            }

    factors: list[str] = []
    for key in ("news", "social", "retail", "analyst", "institutional"):
        for f in (channels.get(key) or {}).get("top_factors") or []:
            if f and f not in factors:
                factors.append(str(f)[:140])
        if len(factors) >= 5:
            break

    alignment_hint = (
        "Narrativa de mercado a favor de largos"
        if label == "bullish"
        else "Narrativa de mercado a favor de cautela / shorts"
        if label == "bearish"
        else "Narrativa de mercado sin sesgo claro"
    )

    return {
        "available": True,
        "source": "sentiment_engine",
        "aggregated_score": round(score, 2),
        "label": label,
        "label_es": _label_es(label),
        "confidence": float(data.get("confidence") or 0.0),
        "summary": data.get("summary") or "",
        "channels": channels,
        "top_factors": factors,
        "sources_used": data.get("sources_used") or [],
        "sources_failed": data.get("sources_failed") or [],
        "alignment_hint": alignment_hint,
        "headline": (
            f"Opinión de mercado { _label_es(label) } "
            f"({score:+.1f}, conf. {float(data.get('confidence') or 0)*100:.0f}%)"
        ),
    }


def build_market_opinion_from_prior(
    prior_ctx: PriorContext | None,
    *,
    sentiment_raw: dict | None = None,
    news_raw: dict | None = None,
) -> dict[str, Any]:
    """Build opinion from committee prior reports (no extra network)."""
    if sentiment_raw:
        op = build_market_opinion_from_engine(sentiment_raw)
        if op.get("available"):
            op["source"] = "committee_sentiment_agent"
            # Blend news agent label if present
            if news_raw:
                news_label = news_raw.get("sentiment_label")
                news_avg = news_raw.get("sentiment_avg_recent")
                if news_avg is not None:
                    op["news_agent_score"] = news_avg
                if news_label:
                    op["news_agent_label"] = news_label
            if prior_ctx:
                op["narrative_score"] = prior_ctx.narrative_score
                op["macro_score"] = prior_ctx.macro_score
            return op

    if not prior_ctx and not news_raw:
        return {"available": False}

    # Fallback: scores only
    sent = prior_ctx.scores.get("sentiment_agent") if prior_ctx else None
    news = prior_ctx.scores.get("news_agent") if prior_ctx else None
    news_avg = None
    news_label = None
    if news_raw:
        news_avg = news_raw.get("sentiment_avg_recent")
        news_label = news_raw.get("sentiment_label")
    if prior_ctx and prior_ctx.news_sentiment_score is not None and news_avg is None:
        news_avg = prior_ctx.news_sentiment_score

    parts = [s for s in (sent, news, news_avg) if s is not None]
    if not parts:
        return {"available": False}

    # Normalize agent scores (~±100) vs news avg (~±1 sometimes) — clamp blend
    normed = []
    for s in parts:
        v = float(s)
        if abs(v) <= 1.5:
            v *= 50.0  # map -1..1 → -50..50
        normed.append(max(-100.0, min(100.0, v)))
    score = sum(normed) / len(normed)
    label = _label_from_score(score)
    summary_bits = []
    if prior_ctx and prior_ctx.summaries.get("sentiment_agent"):
        summary_bits.append(prior_ctx.summaries["sentiment_agent"][:220])
    if prior_ctx and prior_ctx.summaries.get("news_agent"):
        summary_bits.append(prior_ctx.summaries["news_agent"][:220])

    return {
        "available": True,
        "source": "committee_scores",
        "aggregated_score": round(score, 2),
        "label": label,
        "label_es": _label_es(label),
        "confidence": 0.55,
        "summary": " ".join(summary_bits),
        "channels": {
            "sentiment_agent": {"score": sent},
            "news_agent": {"score": news if news is not None else news_avg},
        },
        "top_factors": [],
        "sources_used": [k for k, v in (("sentiment_agent", sent), ("news_agent", news or news_avg)) if v is not None],
        "sources_failed": [],
        "news_agent_label": news_label,
        "narrative_score": prior_ctx.narrative_score if prior_ctx else None,
        "macro_score": prior_ctx.macro_score if prior_ctx else None,
        "alignment_hint": (
            "Narrativa de mercado a favor de largos"
            if label == "bullish"
            else "Narrativa de mercado a favor de cautela / shorts"
            if label == "bearish"
            else "Narrativa de mercado sin sesgo claro"
        ),
        "headline": (
            f"Opinión de mercado {_label_es(label)} "
            f"({score:+.1f} vía comité)"
        ),
    }


def tech_vs_market_alignment(tech_bias: str, market_label: str) -> dict[str, Any]:
    """Compare technical HTF/strategy bias with live market narrative."""
    tech = tech_bias if tech_bias in ("bullish", "bearish") else "neutral"
    mkt = market_label if market_label in ("bullish", "bearish") else "neutral"
    if tech == "neutral" or mkt == "neutral":
        return {
            "status": "neutral",
            "status_es": "sin conflicto claro",
            "note": "Técnico o narrativa sin sesgo fuerte — no hay confirmación cruzada.",
        }
    if tech == mkt:
        return {
            "status": "aligned",
            "status_es": "alineados",
            "note": f"Técnico y opinión de mercado {_label_es(tech)} — confluencia narrativa.",
        }
    return {
        "status": "diverged",
        "status_es": "divergentes",
        "note": (
            f"Técnico {_label_es(tech)} vs mercado {_label_es(mkt)} — "
            "reducir tamaño o esperar resolución."
        ),
    }
