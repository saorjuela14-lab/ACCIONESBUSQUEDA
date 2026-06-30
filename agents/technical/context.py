"""Cross-agent context synthesis for context-aware technical analysis."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding


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

    # Fundamental / valuation alignment
    if tech_dir == fund_dir and tech_dir != "neutral":
        bonus = 8.0 if tech_dir == "bullish" else -4.0
        score_adj += bonus if tech_dir == "bullish" else bonus
        conf_adj += 0.08
        msg = f"Technical {tech_dir} aligns with fundamental/valuation ({ctx.fundamental_score:+.1f})"
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
            f"Divergence: chart {tech_dir} (score {technical_score:+.1f}) vs "
            f"fundamentals {fund_dir} ({ctx.fundamental_score:+.1f})"
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

    # News / sentiment narrative
    if tech_dir != "neutral" and narr_dir != "neutral":
        if tech_dir == narr_dir:
            score_adj += 5.0 if tech_dir == "bullish" else -3.0
            conf_adj += 0.05
            msg = f"Price action confirms news/sentiment narrative ({ctx.narrative_score:+.1f})"
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
                f"Narrative vs price divergence: technical {tech_dir}, "
                f"news/sentiment {narr_dir} ({ctx.narrative_score:+.1f})"
            )
            notes.append(msg)
            conf_adj -= 0.05
            if tech_dir == "bullish" and narr_dir == "bearish":
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=msg + " — rally may lack narrative support",
                        confidence=0.68,
                        references=[],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
            else:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=msg + " — potential contrarian setup if fundamentals hold",
                        confidence=0.62,
                        references=[],
                    )
                )

    # Macro backdrop
    if macro_dir == "bearish" and tech_dir == "bullish":
        msg = f"Macro headwinds ({ctx.macro_score:+.1f}) vs bullish technicals — beta rally risk"
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
        msg = f"Macro tailwinds ({ctx.macro_score:+.1f}) reinforce bullish structure"
        notes.append(msg)
        findings.append(
            Finding(category=EvidenceCategory.INTERPRETATION, statement=msg, confidence=0.7, references=[])
        )

    # Benchmark correlations — technical moves explained by market beta
    for pair in ctx.benchmark_correlations[:4]:
        ticker = pair.get("ticker", "")
        corr = pair.get("correlation")
        if corr is None or abs(corr) < 0.45:
            continue
        if ticker in ("SPY", "QQQ", "IWM"):
            msg = (
                f"High {ticker} correlation ({corr:+.2f}): {daily_bias} move likely "
                f"{'tracks' if corr > 0 else 'inversely tracks'} broad market"
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
                        statement=f"Bullish tape with {ticker} β={corr:+.2f} into bearish macro — fragile",
                        confidence=0.66,
                        references=[],
                    )
                )

    # Macro sensitivity shocks
    for macro in ctx.macro_sensitivities:
        if macro.get("sensitivity") == "high" and tech_dir == "bullish":
            factor = macro.get("factor", "macro")
            msg = f"High {factor} sensitivity: technical strength vulnerable to {macro.get('scenario', 'shock')}"
            notes.append(msg)
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=msg,
                    confidence=0.67,
                    references=[],
                )
            )

    # Company dependency chain
    for dep in ctx.company_dependencies[:3]:
        rel = dep.get("relationship", "")
        corr = dep.get("correlation")
        peer = dep.get("ticker", "")
        if peer and corr is not None and abs(corr) >= 0.4:
            msg = f"Technical read correlated via {peer} ({rel}, ρ={corr:+.2f})"
            notes.append(msg)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"{msg}: {dep.get('why_it_matters', '')[:80]}",
                    confidence=0.64,
                    references=[],
                )
            )

    # RSI confluence with fundamentals
    if daily_rsi is not None:
        if daily_rsi < 35 and ctx.fundamental_score > 10:
            score_adj += 6.0
            conf_adj += 0.06
            msg = f"Oversold RSI {daily_rsi:.1f} + positive fundamentals — mean-reversion confluence"
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
            msg = f"Overbought RSI {daily_rsi:.1f} + weak fundamentals — exhaustion risk"
            notes.append(msg)
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=msg,
                    confidence=0.72,
                    references=[],
                )
            )

    # Elevated company/country risk dampens bullish technicals
    if ctx.risk_score < -15 and tech_dir == "bullish":
        conf_adj -= 0.07
        msg = f"Bullish chart vs elevated risk agents ({ctx.risk_score:+.1f}) — size positions carefully"
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
