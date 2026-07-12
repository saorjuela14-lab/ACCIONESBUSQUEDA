"""Investment memory and agent weight calibration."""

from agents.base import BaseAgent
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from domain.entities import InvestmentMemoryRecord
from domain.enums import EvidenceCategory
from domain.reports import AgentReport, Finding, InvestmentThesis, Reference


class InvestmentMemoryAgent(BaseAgent):
    name = "investment_memory"

    def __init__(self, memory_repo: InvestmentMemoryRepository) -> None:
        self._memory = memory_repo

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        thesis: InvestmentThesis | None = kwargs.get("thesis")
        findings: list[Finding] = []

        if thesis:
            entry_price = kwargs.get("entry_price")
            record = InvestmentMemoryRecord(
                ticker=thesis.ticker,
                thesis=thesis.investment_thesis,
                reasons=[f.category.value for f in thesis.catalysts[:5]],
                scores={r.agent_name: r.score for r in thesis.agent_reports},
                confidence=thesis.confidence,
                scenario=thesis.base_case.name,
                expected_outcome=thesis.base_case.thesis,
                recommendation=thesis.recommendation.value,
                entry_price=entry_price,
            )
            await self._memory.save(record)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Stored investment thesis for {thesis.ticker}",
                    confidence=1.0,
                    references=[Reference(source="investment_memory", data_point="record_id", value=record.id)],
                )
            )

        pending = await self._memory.list_pending_evaluation()
        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"{len(pending)} recommendations pending outcome evaluation",
                confidence=0.9,
                references=[],
            )
        )

        weights = await self._memory.get_agent_weights()

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper() if ticker else None,
            score=0.0,
            confidence=0.85,
            findings=findings,
            risks=[],
            opportunities=[],
            references=[],
            raw_data={"pending_evaluations": len(pending), "agent_weights": weights},
            summary="Memoria de inversión actualizada y seguimiento histórico activo.",
        )

    async def calibrate_weights(self) -> dict[str, float]:
        pending = await self._memory.list_pending_evaluation()
        default_weights = {
            "fundamental_agent": 1.2,
            "technical_agent": 1.0,
            "macro_agent": 0.9,
            "valuation_agent": 1.1,
            "news_agent": 0.8,
            "sentiment_agent": 0.7,
            "country_risk_agent": 0.8,
            "company_risk_agent": 0.9,
            "corporate_actions_agent": 0.7,
        }
        for agent, weight in default_weights.items():
            await self._memory.update_agent_weight(agent, weight, 0.5)
        return default_weights
