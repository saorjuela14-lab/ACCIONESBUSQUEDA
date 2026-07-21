"""Investment Director - sole decision consolidator for the investment committee."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, InvestmentRecommendation, StrategyType, TimeHorizon
from domain.reports import AgentReport, Finding, InvestmentThesis, ScenarioCase, StrategyConclusion
from services.strategy_lab import StrategyLab
from utils.narrative_es import agent_display_name, bias_label, recommendation_label


class InvestmentDirector(BaseAgent):
    """Consolidates agent evidence into an investment thesis. Never analyzes markets directly."""

    name = "investment_director"

    DEFAULT_WEIGHTS: dict[str, float] = {
        "fundamental_agent": 1.2,
        "technical_agent": 1.0,
        "macro_agent": 0.9,
        "valuation_agent": 1.1,
        "news_agent": 0.8,
        "sentiment_agent": 0.7,
        "country_risk_agent": 0.8,
        "company_risk_agent": 0.9,
        "corporate_actions_agent": 0.7,
        "market_dependency_agent": 0.85,
        "portfolio_agent": 1.0,
    }

    def __init__(self, strategy_lab: StrategyLab | None = None) -> None:
        self._strategy_lab = strategy_lab or StrategyLab()

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        reports: list[AgentReport] = kwargs.get("agent_reports", [])
        weights: dict[str, float] = kwargs.get("agent_weights", self.DEFAULT_WEIGHTS)
        quote = kwargs.get("quote", {})
        price = float(quote.get("current_price") or 0)

        thesis = self.build_thesis(ticker, reports, weights, price)

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._weighted_score(reports, weights),
            confidence=thesis.confidence,
            findings=[
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=thesis.executive_summary,
                    confidence=thesis.confidence,
                    references=[],
                ),
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"Recomendación: {recommendation_label(thesis.recommendation)}",
                    confidence=thesis.confidence,
                    references=[],
                ),
            ],
            risks=thesis.risks,
            opportunities=thesis.catalysts,
            references=self._collect_references(reports),
            raw_data={"thesis": thesis.model_dump(mode="json")},
            summary=thesis.executive_summary,
        )

    def build_thesis(
        self,
        ticker: str,
        reports: list[AgentReport],
        weights: dict[str, float],
        current_price: float,
    ) -> InvestmentThesis:
        weighted_score = self._weighted_score(reports, weights)
        avg_confidence = self._avg_confidence(reports)

        # Macro stress → more conservative recommendation mapping
        macro_report = next((r for r in reports if r.agent_name == "macro_agent"), None)
        if macro_report and macro_report.score is not None and macro_report.score <= -8:
            weighted_score -= 8
        elif macro_report and macro_report.score is not None and macro_report.score <= -4:
            weighted_score -= 4

        recommendation = self._map_recommendation(weighted_score)

        all_risks = []
        all_catalysts = []
        for report in reports:
            all_risks.extend(report.risks)
            all_catalysts.extend(report.opportunities)

        price_target = self._estimate_target(reports, current_price, weighted_score)
        bull_prob, base_prob, bear_prob = self._scenario_probabilities(weighted_score)

        bull = ScenarioCase(
            name="Escenario Alcista",
            probability=bull_prob,
            price_target=price_target * 1.2 if price_target else None,
            thesis=self._build_case_narrative("alcista", reports, weighted_score),
            catalysts=[c.statement for c in all_catalysts[:3]],
            risks=[r.statement for r in all_risks[:2]],
            confidence=avg_confidence * 0.85,
        )
        bear = ScenarioCase(
            name="Escenario Bajista",
            probability=bear_prob,
            price_target=price_target * 0.8 if price_target else None,
            thesis=self._build_case_narrative("bajista", reports, weighted_score),
            catalysts=[],
            risks=[r.statement for r in all_risks[:4]],
            confidence=avg_confidence * 0.85,
        )
        base = ScenarioCase(
            name="Escenario Base",
            probability=base_prob,
            price_target=price_target,
            thesis=self._build_case_narrative("base", reports, weighted_score),
            catalysts=[c.statement for c in all_catalysts[:2]],
            risks=[r.statement for r in all_risks[:2]],
            confidence=avg_confidence,
        )

        strategy_conclusions = self._strategy_lab.evaluate(reports)

        return InvestmentThesis(
            ticker=ticker.upper(),
            executive_summary=(
                f"Síntesis del comité de inversión para {ticker.upper()}: "
                f"puntuación ponderada {weighted_score:.1f}/100, "
                f"recomendación {recommendation_label(recommendation)} "
                f"con {avg_confidence * 100:.0f}% de confianza basada en {len(reports)} informes de agentes."
            ),
            investment_thesis=self._build_thesis_narrative(reports, weighted_score, recommendation),
            bull_case=bull,
            bear_case=bear,
            base_case=base,
            catalysts=all_catalysts[:8],
            risks=all_risks[:8],
            recommendation=recommendation,
            confidence=self._clamp_confidence(avg_confidence),
            price_target=price_target,
            agent_reports=reports,
            strategy_conclusions=strategy_conclusions,
        )

    def _weighted_score(self, reports: list[AgentReport], weights: dict[str, float]) -> float:
        total_weight = 0.0
        weighted = 0.0
        for report in reports:
            if report.agent_name == self.name:
                continue
            w = weights.get(report.agent_name, 1.0)
            weighted += report.score * w * report.confidence
            total_weight += w * report.confidence
        if total_weight == 0:
            return 0.0
        return self._clamp_score(weighted / total_weight)

    def _avg_confidence(self, reports: list[AgentReport]) -> float:
        filtered = [r for r in reports if r.agent_name != self.name]
        if not filtered:
            return 0.3
        return self._clamp_confidence(sum(r.confidence for r in filtered) / len(filtered))

    def _map_recommendation(self, score: float) -> InvestmentRecommendation:
        if score >= 40:
            return InvestmentRecommendation.STRONG_BUY
        if score >= 15:
            return InvestmentRecommendation.BUY
        if score >= -15:
            return InvestmentRecommendation.HOLD
        if score >= -40:
            return InvestmentRecommendation.SELL
        return InvestmentRecommendation.STRONG_SELL

    def _estimate_target(self, reports: list[AgentReport], price: float, score: float) -> float | None:
        if not price:
            return None
        for report in reports:
            if report.agent_name == "valuation_agent":
                fair = report.raw_data.get("fair_value")
                if fair:
                    return float(fair)
        return price * (1 + score / 200)

    def _scenario_probabilities(self, score: float) -> tuple[float, float, float]:
        normalized = (score + 100) / 200
        bull = min(0.6, max(0.1, normalized * 0.5))
        bear = min(0.6, max(0.1, (1 - normalized) * 0.5))
        base = max(0.1, 1.0 - bull - bear)
        total = bull + bear + base
        return bull / total, base / total, bear / total

    def _build_case_narrative(self, case: str, reports: list[AgentReport], score: float) -> str:
        top_agents = sorted(reports, key=lambda r: abs(r.score), reverse=True)[:3]
        evidence = "; ".join(
            f"{agent_display_name(r.agent_name)}: {r.summary[:80]}" for r in top_agents
        )
        return f"Escenario {case} (puntuación {score:.1f}) respaldado por: {evidence}"

    def _build_thesis_narrative(
        self, reports: list[AgentReport], score: float, recommendation: InvestmentRecommendation
    ) -> str:
        facts = []
        interpretations = []
        for report in reports:
            for finding in report.findings[:2]:
                if finding.category == EvidenceCategory.FACT:
                    facts.append(finding.statement)
                else:
                    interpretations.append(finding.statement)

        return (
            f"El comité recomienda {recommendation_label(recommendation)} "
            f"con puntuación compuesta {score:.1f}. "
            f"Hechos verificados: {' | '.join(facts[:4]) or 'limitados'}. "
            f"Interpretaciones: {' | '.join(interpretations[:3]) or 'ninguna'}."
        )

    def _collect_references(self, reports: list[AgentReport]) -> list:
        refs = []
        for report in reports:
            refs.extend(report.references[:3])
        return refs[:20]
