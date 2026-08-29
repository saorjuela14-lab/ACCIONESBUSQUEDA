"""Compact per-agent briefs, close-out critique, and next-committee lesson clip.

The desk must remember *why* a member was wrong — not only the score sign —
and must not reuse the same justification on the next hunt.
"""

from __future__ import annotations

from typing import Any

from domain.enums import EvidenceCategory
from domain.reports import AgentReport, Finding

SCORE_THRESHOLD = 5.0
CLIP_PTS = 12.0
SUMMARY_MAX = 400
FINDING_MAX = 180

PATTERN_NEEDLES: dict[str, tuple[str, ...]] = {
    "breakout_failed": (
        "ruptura",
        "breakout",
        "rompe",
        "rompe máximos",
        "resistencia rota",
        "break-out",
        "break out",
    ),
    "oversold_failed": ("sobreventa", "oversold", "rebote", "rsi"),
    "catalyst_failed": (
        "catalyst",
        "catalizador",
        "earnings",
        "resultados",
        "noticia",
        "evento",
    ),
    "sentiment_failed": (
        "sentimiento",
        "social",
        "reddit",
        "stocktwits",
        "hype",
        "retail",
    ),
    "gap_failed": ("gap", "hueco", "gap-up", "gap up", "gapdown", "gap-down"),
    "false_long": ("alcista", "compra", "long", "bullish", "momentum"),
    "false_veto": ("veto", "evitar", "bajista", "short", "sobrevalor", "caro"),
    "stagnation_failed": ("estanc", "sin avance", "no progres", "momentum", "hold"),
}

_SKIP_AGENTS = {"investment_director"}


def _finding_text(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("statement") or item.get("text") or "").strip()
    return str(getattr(item, "statement", None) or item or "").strip()


def original_score(report: AgentReport) -> float:
    raw = report.raw_data or {}
    if "pre_lesson_score" in raw:
        try:
            return float(raw["pre_lesson_score"])
        except (TypeError, ValueError):
            pass
    return float(report.score)


def compact_agent_briefs(reports: list[AgentReport] | None) -> dict[str, dict[str, Any]]:
    """Persistable justification snapshot (score + why), compact enough for SQLite/Neon."""
    out: dict[str, dict[str, Any]] = {}
    for report in reports or []:
        name = (report.agent_name or "").strip()
        if not name or name in _SKIP_AGENTS:
            continue
        score = original_score(report)
        if score >= 8:
            stance = "long"
        elif score <= -8:
            stance = "short"
        else:
            stance = "neutral"
        snap = (report.raw_data or {}).get("pre_lesson_snapshot") or {}
        summary = str(snap.get("summary") or report.summary or "")[:SUMMARY_MAX]
        if isinstance(snap.get("findings"), list) and snap["findings"]:
            raw_findings = snap["findings"]
        else:
            raw_findings = [_finding_text(x) for x in (report.findings or [])]
        if isinstance(snap.get("risks"), list) and snap["risks"]:
            raw_risks = snap["risks"]
        else:
            raw_risks = [_finding_text(x) for x in (report.risks or [])]
        findings = [
            str(x)[:FINDING_MAX]
            for x in raw_findings[:4]
            if str(x).strip() and not str(x).startswith("LECCIÓN")
        ]
        risks = [
            str(x)[:FINDING_MAX]
            for x in raw_risks[:4]
            if str(x).strip() and not str(x).startswith("LECCIÓN")
        ]
        out[name] = {
            "score": round(score, 1),
            "confidence": round(float(report.confidence or 0), 3),
            "summary": summary,
            "findings": [f for f in findings if f],
            "risks": [r for r in risks if r],
            "stance": stance,
        }
    return out


def infer_pattern(summary: str, findings: list[str] | None = None) -> str:
    text = " ".join([summary or ""] + list(findings or [])).lower()
    for pattern, needles in PATTERN_NEEDLES.items():
        if pattern in ("false_long", "false_veto"):
            continue
        if any(n in text for n in needles):
            return pattern
    return "false_long"


def _join_bits(*parts: str, limit: int = 420) -> str:
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return text[:limit].rstrip()


def critique_agent(
    *,
    agent_name: str,
    brief: dict[str, Any] | None,
    outcome: str,
    outcome_tag: str,
    ticker: str,
    pnl_pct: float | None = None,
) -> dict[str, Any]:
    """Compare one member's justification vs operation outcome (TP / stop)."""
    brief = dict(brief or {})
    try:
        score = float(brief.get("score") if brief.get("score") is not None else 0)
    except (TypeError, ValueError):
        score = 0.0
    summary = str(brief.get("summary") or "").strip()
    findings = [str(x) for x in (brief.get("findings") or []) if str(x).strip()]
    risks = [str(x) for x in (brief.get("risks") or []) if str(x).strip()]
    has_brief = bool(summary or findings or risks)
    pnl_s = f"{float(pnl_pct):+.1f}%" if pnl_pct is not None else "n/d"
    why_core = summary[:220] if summary else (findings[0][:180] if findings else "")
    if not has_brief:
        why_core = "sin justificación guardada; se evalúa solo el sesgo (score)"

    base = {
        "agent": agent_name,
        "score": round(score, 1),
        "has_brief": has_brief,
        "justification": why_core,
        "pattern": None,
        "verdict": "neutral",
        "why": "",
    }

    if outcome == "gestion" or abs(score) < SCORE_THRESHOLD:
        base["why"] = (
            f"{agent_name}: cierre de gestión o score débil ({score:+.0f}) — "
            "no hay veredicto de justificación."
        )
        return base

    findings_s = "; ".join(findings[:3])
    risks_s = "; ".join(risks[:2])

    if outcome == "stagnation":
        if score >= SCORE_THRESHOLD:
            base["verdict"] = "wrong"
            base["pattern"] = "stagnation_failed"
            base["why"] = _join_bits(
                f"{agent_name} apoyó el largo ({score:+.0f}) y {ticker} se estancó ({outcome_tag}, PnL {pnl_s}).",
                "Error: ocupó el capital ultra-micro sin avanzar el umbral de la mesa (1.5%).",
                f"Justificación que no pagó: {why_core}." if why_core else "",
                f"Hallazgos: {findings_s}." if findings_s else "",
                "Ajuste: no recomprar el mismo ticker ni reutilizar esa tesis hasta que haya un setup 2R nuevo.",
            )
        else:
            base["verdict"] = "correct"
            base["why"] = _join_bits(
                f"{agent_name} advirtió en contra ({score:+.0f}) y {ticker} no avanzó ({pnl_s}).",
                "Acertó: no había que sentarse en ese trade.",
            )
        return base

    if outcome == "win":
        if score >= SCORE_THRESHOLD:
            base["verdict"] = "correct"
            base["why"] = _join_bits(
                f"{agent_name} apoyó la tesis larga ({score:+.0f}) y la operación se ganó por {outcome_tag} (PnL {pnl_s}).",
                f"Justificación: {why_core}." if why_core else "",
                f"Hallazgos: {findings_s}." if findings_s else "",
            )
        else:
            base["verdict"] = "wrong"
            base["pattern"] = "false_veto"
            base["why"] = _join_bits(
                f"{agent_name} se opuso ({score:+.0f}) y {ticker} se ganó por {outcome_tag} (PnL {pnl_s}).",
                "Error: vetó o descontó un trade que sí pagó.",
                f"Dijo: {why_core}." if why_core else "",
                f"Hallazgos: {findings_s}." if findings_s else "",
                "Ajuste: no reutilizar el mismo veto si el setup 2R vuelve a aparecer.",
            )
        return base

    # loss
    if score >= SCORE_THRESHOLD:
        pattern = infer_pattern(summary, findings)
        base["verdict"] = "wrong"
        base["pattern"] = pattern
        base["why"] = _join_bits(
            f"{agent_name} apoyó el largo ({score:+.0f}) y {ticker} se perdió por {outcome_tag} (PnL {pnl_s}).",
            f"Patrón: {pattern}.",
            f"Justificación fallida: {why_core}." if why_core else "",
            f"Hallazgos que no se confirmaron: {findings_s}." if findings_s else "",
            f"Riesgos que mencionó y no pesó: {risks_s}." if risks_s else "",
            "Ajuste: recortar ese argumento la próxima vez y exigir confirmación extra.",
        )
        return base

    base["verdict"] = "correct"
    base["why"] = _join_bits(
        f"{agent_name} advirtió en contra ({score:+.0f}) y {ticker} se perdió por {outcome_tag} (PnL {pnl_s}).",
        f"Acertó: {why_core}." if why_core else "El sesgo bajista coincidió con el stop.",
    )
    return base


def _report_text(report: AgentReport) -> str:
    parts = [report.summary or ""]
    for item in list(report.findings or []) + list(report.risks or []):
        parts.append(_finding_text(item))
    return " ".join(parts).lower()


def _lesson_overlaps(report: AgentReport, lesson: dict[str, Any]) -> bool:
    text = _report_text(report)
    pattern = str(lesson.get("pattern") or "")
    needles = PATTERN_NEEDLES.get(pattern, ())
    if needles and any(n in text for n in needles):
        return True
    ticker = str(lesson.get("ticker") or "").lower()
    if ticker and ticker in text:
        return True
    for bit in list(lesson.get("findings") or [])[:4]:
        token = str(bit).strip().lower()
        if len(token) >= 12 and token[:40] in text:
            return True
    return False


def apply_agent_error_lessons(
    reports: list[AgentReport],
    errors_by_agent: dict[str, list[dict[str, Any]]] | None,
) -> list[AgentReport]:
    """Inject last errors as RISK and clip score if the same justification reappears."""
    if not errors_by_agent:
        return reports
    for report in reports:
        name = (report.agent_name or "").strip()
        errors = errors_by_agent.get(name) or []
        if not errors:
            continue
        raw = dict(report.raw_data or {})
        if "pre_lesson_score" not in raw:
            raw["pre_lesson_score"] = float(report.score)
        extra_findings: list[Finding] = []
        extra_risks: list[Finding] = []
        clip = 0.0
        patterns_hit: list[str] = []
        if "pre_lesson_snapshot" not in raw:
            raw["pre_lesson_snapshot"] = {
                "summary": report.summary or "",
                "findings": [_finding_text(x) for x in (report.findings or [])[:6]],
                "risks": [_finding_text(x) for x in (report.risks or [])[:6]],
            }
        for lesson in errors[:4]:
            reason = str(lesson.get("reason") or "").strip()
            if not reason:
                continue
            extra_findings.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"LECCIÓN: {reason[:220]}",
                    confidence=0.92,
                )
            )
            extra_risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=reason[:160],
                    confidence=0.9,
                )
            )
            if _lesson_overlaps(report, lesson):
                clip += CLIP_PTS
                pat = str(lesson.get("pattern") or "error")
                if pat not in patterns_hit:
                    patterns_hit.append(pat)
        if clip:
            original = float(report.score)
            if original > 0:
                report.score = max(0.0, original - clip)
            elif original < 0:
                report.score = min(0.0, original + clip)
            extra_findings.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=(
                        f"Score recortado {clip:.0f} pts "
                        f"({original:+.0f} → {report.score:+.0f}): "
                        "no repetir el mismo error de justificación "
                        f"({', '.join(patterns_hit) or 'patrón previo'})."
                    ),
                    confidence=0.95,
                )
            )
            raw["lesson_clip"] = clip
            raw["lesson_patterns"] = patterns_hit
        raw["lesson_applied"] = True
        report.raw_data = raw
        report.findings = extra_findings + list(report.findings or [])
        report.risks = extra_risks + list(report.risks or [])
        if extra_findings:
            note = " | memoria de error aplicada"
            if note not in (report.summary or ""):
                report.summary = ((report.summary or "").rstrip() + note)[:SUMMARY_MAX]
    return reports
