"""Committee consensus gates for autonomous capital management.

A name may be recommended or bought only when:
1. Every voting agent maps to BUY / STRONG_BUY
2. Short-horizon strategies (momentum, swing, breakout) all map to BUY
3. Long-horizon strategies (value, growth) all map to BUY
   (dividend is informational for pennies — must not be SELL)
4. Director thesis recommendation is BUY / STRONG_BUY
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.investment_director import InvestmentDirector
from domain.enums import InvestmentRecommendation, StrategyType
from domain.reports import InvestmentThesis, StrategyConclusion

# Core market agents — portfolio / watchlist / alert / memory are book context, not votes.
VOTING_AGENTS: frozenset[str] = frozenset(
    {
        "fundamental_agent",
        "technical_agent",
        "valuation_agent",
        "macro_agent",
        "news_agent",
        "sentiment_agent",
        "country_risk_agent",
        "company_risk_agent",
        "corporate_actions_agent",
        "market_dependency_agent",
    }
)

SHORT_STRATEGIES: frozenset[StrategyType] = frozenset(
    {
        StrategyType.MOMENTUM,
        StrategyType.SWING,
        StrategyType.BREAKOUT,
    }
)

LONG_STRATEGIES: frozenset[StrategyType] = frozenset(
    {
        StrategyType.VALUE,
        StrategyType.GROWTH,
    }
)

# Dividend often N/A for micro/pennies — block only if actively bearish.
LONG_OPTIONAL: frozenset[StrategyType] = frozenset({StrategyType.DIVIDEND})

BUY_THRESHOLD = 15.0  # matches InvestmentDirector._map_recommendation
SELL_THRESHOLD = -15.0

SOURCE_TAG = "committee_unanimous_dual"


def score_to_recommendation(score: float) -> InvestmentRecommendation:
    return InvestmentDirector()._map_recommendation(score)


def is_buy(rec: InvestmentRecommendation) -> bool:
    return rec in (InvestmentRecommendation.BUY, InvestmentRecommendation.STRONG_BUY)


def is_sell(rec: InvestmentRecommendation) -> bool:
    return rec in (InvestmentRecommendation.SELL, InvestmentRecommendation.STRONG_SELL)


@dataclass
class ConsensusVerdict:
    passed: bool
    thesis_buy: bool = False
    agents_unanimous_buy: bool = False
    short_horizon_buy: bool = False
    long_horizon_buy: bool = False
    agent_votes: dict[str, str] = field(default_factory=dict)
    short_scores: dict[str, float] = field(default_factory=dict)
    long_scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    recommendation: str | None = None

    @property
    def source_tag(self) -> str:
        return SOURCE_TAG if self.passed else "committee_rejected"


def evaluate_consensus(thesis: InvestmentThesis) -> ConsensusVerdict:
    """Hard gate: full voting-agent BUY + short/long strategy BUY."""
    reasons: list[str] = []
    agent_votes: dict[str, str] = {}

    voting_reports = [
        r for r in (thesis.agent_reports or []) if r.agent_name in VOTING_AGENTS
    ]
    missing = sorted(VOTING_AGENTS - {r.agent_name for r in voting_reports})
    if missing:
        reasons.append(f"Faltan votos del comité: {', '.join(missing)}")

    agents_ok = True
    for report in voting_reports:
        rec = score_to_recommendation(float(report.score))
        agent_votes[report.agent_name] = rec.value
        if not is_buy(rec):
            agents_ok = False
            reasons.append(
                f"{report.agent_name}={rec.value} (score {report.score:.1f}) — se requiere BUY"
            )

    if not voting_reports:
        agents_ok = False
        reasons.append("Sin informes de agentes votantes")

    short_scores, short_ok = _horizon_bucket_ok(
        thesis.strategy_conclusions or [],
        SHORT_STRATEGIES,
        label="corto plazo",
        reasons=reasons,
    )
    long_scores, long_ok = _long_bucket_ok(
        thesis.strategy_conclusions or [],
        reasons=reasons,
    )

    thesis_rec = thesis.recommendation
    thesis_buy = is_buy(thesis_rec) if thesis_rec else False
    if not thesis_buy:
        reasons.append(
            f"Tesis del director={thesis_rec.value if thesis_rec else 'n/a'} — se requiere BUY"
        )

    passed = bool(agents_ok and short_ok and long_ok and thesis_buy and not missing)
    return ConsensusVerdict(
        passed=passed,
        thesis_buy=thesis_buy,
        agents_unanimous_buy=agents_ok and not missing,
        short_horizon_buy=short_ok,
        long_horizon_buy=long_ok,
        agent_votes=agent_votes,
        short_scores=short_scores,
        long_scores=long_scores,
        reasons=reasons,
        recommendation=thesis_rec.value if thesis_rec else None,
    )


def _horizon_bucket_ok(
    conclusions: list[StrategyConclusion],
    wanted: frozenset[StrategyType],
    *,
    label: str,
    reasons: list[str],
) -> tuple[dict[str, float], bool]:
    scores: dict[str, float] = {}
    present = [c for c in conclusions if c.strategy in wanted]
    if len(present) < len(wanted):
        missing = sorted(s.value for s in wanted - {c.strategy for c in present})
        reasons.append(f"Estrategias {label} incompletas: {', '.join(missing)}")
        return scores, False
    ok = True
    for c in present:
        scores[c.strategy.value] = float(c.score)
        rec = score_to_recommendation(float(c.score))
        if not is_buy(rec):
            ok = False
            reasons.append(
                f"Estrategia {c.strategy.value} ({label})={rec.value} "
                f"(score {c.score:.1f}) — se requiere BUY"
            )
    return scores, ok


def _long_bucket_ok(
    conclusions: list[StrategyConclusion],
    *,
    reasons: list[str],
) -> tuple[dict[str, float], bool]:
    scores: dict[str, float] = {}
    required = [c for c in conclusions if c.strategy in LONG_STRATEGIES]
    optional = [c for c in conclusions if c.strategy in LONG_OPTIONAL]

    if len(required) < len(LONG_STRATEGIES):
        missing = sorted(s.value for s in LONG_STRATEGIES - {c.strategy for c in required})
        reasons.append(f"Estrategias largo plazo incompletas: {', '.join(missing)}")
        return scores, False

    ok = True
    for c in required:
        scores[c.strategy.value] = float(c.score)
        rec = score_to_recommendation(float(c.score))
        if not is_buy(rec):
            ok = False
            reasons.append(
                f"Estrategia {c.strategy.value} (largo plazo)={rec.value} "
                f"(score {c.score:.1f}) — se requiere BUY"
            )
    for c in optional:
        scores[c.strategy.value] = float(c.score)
        if float(c.score) <= SELL_THRESHOLD:
            ok = False
            reasons.append(
                f"Estrategia dividend={score_to_recommendation(float(c.score)).value} "
                f"— bloquea compra en largo plazo"
            )
    return scores, ok


def attach_consensus_to_pick_fields(verdict: ConsensusVerdict) -> dict:
    """Fields to merge onto TradePick / rationale."""
    return {
        "committee_unanimous": verdict.passed,
        "committee_recommendation": verdict.recommendation,
        "short_horizon_buy": verdict.short_horizon_buy,
        "long_horizon_buy": verdict.long_horizon_buy,
        "sources_extra": [verdict.source_tag],
    }
