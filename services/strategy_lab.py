"""Strategy Lab - independent strategy conclusions."""

from domain.enums import StrategyType, TimeHorizon
from domain.reports import AgentReport, StrategyConclusion


class StrategyLab:
    """Evaluates ticker suitability across multiple investment strategies."""

    async def evaluate_async(self, reports: list[AgentReport]) -> list[StrategyConclusion]:
        return self.evaluate(reports)

    def evaluate(self, reports: list[AgentReport]) -> list[StrategyConclusion]:
        by_agent = {r.agent_name: r for r in reports}
        fundamental = by_agent.get("fundamental_agent")
        technical = by_agent.get("technical_agent")
        valuation = by_agent.get("valuation_agent")
        corporate = by_agent.get("corporate_actions_agent")
        sentiment = by_agent.get("sentiment_agent")

        conclusions: list[StrategyConclusion] = []

        conclusions.append(self._conclude(
            StrategyType.VALUE,
            valuation,
            fundamental,
            TimeHorizon.LONG_TERM,
            "Fuerte para value" if self._score(valuation) > 10 and self._score(fundamental) > 0 else "Neutral para value",
        ))
        conclusions.append(self._conclude(
            StrategyType.GROWTH,
            fundamental,
            sentiment,
            TimeHorizon.LONG_TERM,
            "Fuerte para growth" if self._score(fundamental) > 15 else "Débil para growth",
        ))
        conclusions.append(self._conclude(
            StrategyType.DIVIDEND,
            corporate,
            fundamental,
            TimeHorizon.LONG_TERM,
            "Fuerte para dividendos" if self._score(corporate) > 5 else "Débil para dividendos",
        ))
        conclusions.append(self._conclude(
            StrategyType.MOMENTUM,
            technical,
            sentiment,
            TimeHorizon.WEEKLY,
            "Fuerte para momentum" if self._score(technical) > 15 and self._score(sentiment) > 0 else "Débil para momentum",
        ))
        conclusions.append(self._conclude(
            StrategyType.SWING,
            technical,
            None,
            TimeHorizon.WEEKLY,
            "Adecuado para swing" if self._score(technical) > 5 else "Pobre para swing",
        ))
        conclusions.append(self._conclude(
            StrategyType.BREAKOUT,
            technical,
            None,
            TimeHorizon.INTRADAY,
            "Setup de breakout" if self._score(technical) > 20 else "Sin setup de breakout",
        ))
        conclusions.append(self._conclude(
            StrategyType.MEAN_REVERSION,
            technical,
            valuation,
            TimeHorizon.WEEKLY,
            "Candidato a reversión a la media" if self._score(technical) < -10 and self._score(valuation) > 0 else "No ideal",
        ))
        conclusions.append(self._conclude(
            StrategyType.SECTOR_ROTATION,
            by_agent.get("macro_agent"),
            fundamental,
            TimeHorizon.MONTHLY,
            "Rotación sectorial favorable" if self._score(by_agent.get("macro_agent")) > 0 else "Neutral",
        ))
        conclusions.append(self._conclude(
            StrategyType.SMART_MONEY,
            technical,
            corporate,
            TimeHorizon.MONTHLY,
            "Posible alineación institucional" if self._score(corporate) > 5 else "Sin señal smart money",
        ))

        return conclusions

    def _score(self, report: AgentReport | None) -> float:
        return report.score if report else 0.0

    def _conclude(
        self,
        strategy: StrategyType,
        primary: AgentReport | None,
        secondary: AgentReport | None,
        horizon: TimeHorizon,
        label: str,
    ) -> StrategyConclusion:
        score = self._score(primary) * 0.7 + self._score(secondary) * 0.3 if secondary else self._score(primary)
        confidence = 0.5
        if primary:
            confidence = primary.confidence
        if secondary:
            confidence = (confidence + secondary.confidence) / 2
        return StrategyConclusion(
            strategy=strategy,
            score=max(-100, min(100, score)),
            confidence=max(0, min(1, confidence)),
            conclusion=label,
            horizon=horizon,
        )
