"""Cross-agent context synthesis for context-aware technical analysis."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding
from utils.narrative_es import bias_label


@dataclass
class PriorContext:
    """Condensed evidence from agents that run before technical analysis."""

    scores: dict[str, float] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)
    benchmark_correlations: list[dict] = field(default_factory=list)
    macro_sensitivities: list[dict] = field(default_factory=list)
    company_dependencies: list[dict] = field(default_factory=list)
    news_sentiment_score: float | None = None
    key_risks: list[str] = field(default_factory=list)
    key_opportunities: list[str] = field(default_factory=list)

    @property
    def fundamental_score(self) -> float:
        vals = [self.scores[a] for a in ("fundamental_agent", "valuation_agent") if a in self.scores]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def narrative_score(self) -> float:
        vals = [self.scores[a] for a in ("news_agent", "sentiment_agent") if a in self.scores]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def macro_score(self) -> float:
        vals = [self.scores[a] for a in ("macro_agent", "country_risk_agent") if a in self.scores]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def risk_score(self) -> float:
        vals = [self.scores[a] for a in ("company_risk_agent", "corporate_actions_agent") if a in self.scores]
        return sum(vals) / len(vals) if vals else 0.0


def build_prior_context(prior_reports: list[AgentReport]) -> PriorContext:
    ctx = PriorContext()
    for report in prior_reports:
        ctx.scores[report.agent_name] = report.score
        ctx.summaries[report.agent_name] = report.summary
        for risk in report.risks[:2]:
            ctx.key_risks.append(f"[{report.agent_name}] {risk.statement[:120]}")
        for opp in report.opportunities[:2]:
            ctx.key_opportunities.append(f"[{report.agent_name}] {opp.statement[:120]}")

        if report.agent_name == "market_dependency_agent":
            raw = report.raw_data or {}
            ctx.benchmark_correlations = raw.get("benchmark_correlations", [])
            ctx.macro_sensitivities = raw.get("macro_sensitivities", [])
            ctx.company_dependencies = raw.get("company_dependencies", [])

        if report.agent_name == "news_agent":
            raw = report.raw_data or {}
            ctx.news_sentiment_score = raw.get("sentiment_score") or report.score

    return ctx


def _direction(score: float, threshold: float = 8.0) -> str:
    if score >= threshold:
        return "bullish"
    if score <= -threshold:
        return "bearish"
    return "neutral"


@dataclass
class ContextCorrelationResult:
    score_adjustment: float
    confidence_adjustment: float
    findings: list[Finding]
    risks: list[Finding]
    opportunities: list[Finding]
    correlation_notes: list[str]


def correlate_technical_with_context(
    technical_score: float,
    daily_bias: str,
    daily_rsi: float | None,
    ctx: PriorContext,
) -> ContextCorrelationResult:
    """Adjust technical interpretation using full prior-agent context."""
    findings: list[Finding] = []
    risks: list[Finding] = []
    opportunities: list[Finding] = []
    notes: list[str] = []
    score_adj = 0.0
    conf_adj = 0.0

    tech_dir = _direction(technical_score)
    fund_dir = _direction(ctx.fundamental_score)
    narr_dir = _direction(ctx.narrative_score)
    macro_dir = _direction(ctx.macro_score)

    if tech_dir == fund_dir and tech_dir != "neutral":
        bonus = 8.0 if tech_dir == "bullish" else -4.0
        score_adj += bonus if tech_dir == "bullish" else bonus
        conf_adj += 0.08
        msg = (
            f"Técnico {bias_label(tech_dir)} alineado con fundamental/valoración "
            f"({ctx.fundamental_score:+.1f})"
        )
        notes.append(msg)
        findings.append(
            Finding(
                category=EvidenceCategory.INTERPRETATION,
                statement=msg,
                confidence=0.75,
                references=[],
            )
        )
    elif tech_dir != "neutral" and fund_dir != "neutral" and tech_dir != fund_dir:
        msg = (
            f"Divergencia: gráfico {bias_label(tech_dir)} (puntuación {technical_score:+.1f}) vs "
            f"fundamentales {bias_label(fund_dir)} ({ctx.fundamental_score:+.1f})"
        )
        notes.append(msg)
        conf_adj -= 0.06
        findings.append(
            Finding(
                category=EvidenceCategory.INTERPRETATION,
                statement=msg,
                confidence=0.7,
                references=[],
                impact=ImpactLevel.MEDIUM,
            )
        )

    if tech_dir != "neutral" and narr_dir != "neutral":
        if tech_dir == narr_dir:
            score_adj += 5.0 if tech_dir == "bullish" else -3.0
            conf_adj += 0.05
            msg = f"El precio confirma la narrativa de noticias/sentimiento ({ctx.narrative_score:+.1f})"
            notes.append(msg)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=msg,
                    confidence=0.72,
                    references=[],
                )
            )
        elif tech_dir != narr_dir:
            msg = (
                f"Divergencia narrativa vs precio: técnico {bias_label(tech_dir)}, "
                f"noticias/sentimiento {bias_label(narr_dir)} ({ctx.narrative_score:+.1f})"
            )
            notes.append(msg)
            conf_adj -= 0.05
            if tech_dir == "bullish" and narr_dir == "bearish":
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=msg + " — el rally puede carecer de soporte narrativo",
                        confidence=0.68,
                        references=[],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
            else:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=msg + " — posible setup contrarian si los fundamentales se mantienen",
                        confidence=0.62,
                        references=[],
                    )
                )

    if macro_dir == "bearish" and tech_dir == "bullish":
        msg = f"Vientos macro en contra ({ctx.macro_score:+.1f}) vs técnico alcista — riesgo de rally beta"
        notes.append(msg)
        conf_adj -= 0.04
        risks.append(
            Finding(
                category=EvidenceCategory.RISK,
                statement=msg,
                confidence=0.65,
                references=[],
            )
        )
    elif macro_dir == "bullish" and tech_dir == "bullish":
        score_adj += 4.0
        conf_adj += 0.04
        msg = f"Vientos macro a favor ({ctx.macro_score:+.1f}) refuerzan estructura alcista"
        notes.append(msg)
        findings.append(
            Finding(category=EvidenceCategory.INTERPRETATION, statement=msg, confidence=0.7, references=[])
        )

    for pair in ctx.benchmark_correlations[:4]:
        ticker = pair.get("ticker", "")
        corr = pair.get("correlation")
        if corr is None or abs(corr) < 0.45:
            continue
        if ticker in ("SPY", "QQQ", "IWM"):
            msg = (
                f"Alta correlación con {ticker} ({corr:+.2f}): movimiento {bias_label(daily_bias)} "
                f"probablemente {'sigue' if corr > 0 else 'sigue inversamente'} al mercado amplio"
            )
            notes.append(msg)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=msg,
                    confidence=0.78,
                    references=[],
                )
            )
            if macro_dir == "bearish" and corr > 0.5 and tech_dir == "bullish":
                conf_adj -= 0.03
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"Tape alcista con β {ticker}={corr:+.2f} en macro bajista — frágil",
                        confidence=0.66,
                        references=[],
                    )
                )

    for macro in ctx.macro_sensitivities:
        if macro.get("sensitivity") == "high" and tech_dir == "bullish":
            factor = macro.get("factor", "macro")
            msg = f"Alta sensibilidad a {factor}: fortaleza técnica vulnerable a {macro.get('scenario', 'shock')}"
            notes.append(msg)
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=msg,
                    confidence=0.67,
                    references=[],
                )
            )

    for dep in ctx.company_dependencies[:3]:
        rel = dep.get("relationship", "")
        corr = dep.get("correlation")
        peer = dep.get("ticker", "")
        if peer and corr is not None and abs(corr) >= 0.4:
            msg = f"Lectura técnica correlacionada vía {peer} ({rel}, ρ={corr:+.2f})"
            notes.append(msg)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"{msg}: {dep.get('why_it_matters', '')[:80]}",
                    confidence=0.64,
                    references=[],
                )
            )

    if daily_rsi is not None:
        if daily_rsi < 35 and ctx.fundamental_score > 10:
            score_adj += 6.0
            conf_adj += 0.06
            msg = f"RSI sobrevendido {daily_rsi:.1f} + fundamentales positivos — confluencia de reversión a la media"
            notes.append(msg)
            opportunities.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=msg,
                    confidence=0.74,
                    references=[],
                    impact=ImpactLevel.MEDIUM,
                )
            )
        elif daily_rsi > 65 and ctx.fundamental_score < -10:
            score_adj -= 5.0
            msg = f"RSI sobrecomprado {daily_rsi:.1f} + fundamentales débiles — riesgo de agotamiento"
            notes.append(msg)
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=msg,
                    confidence=0.72,
                    references=[],
                )
            )

    if ctx.risk_score < -15 and tech_dir == "bullish":
        conf_adj -= 0.07
        msg = f"Gráfico alcista vs agentes de riesgo elevado ({ctx.risk_score:+.1f}) — dimensionar posiciones con cuidado"
        notes.append(msg)
        risks.append(
            Finding(category=EvidenceCategory.RISK, statement=msg, confidence=0.7, references=[])
        )

    return ContextCorrelationResult(
        score_adjustment=score_adj,
        confidence_adjustment=conf_adj,
        findings=findings,
        risks=risks,
        opportunities=opportunities,
        correlation_notes=notes,
    )
