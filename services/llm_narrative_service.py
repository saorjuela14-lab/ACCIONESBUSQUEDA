"""Optional LLM narrative for executive reports."""

from __future__ import annotations

import httpx

from config.settings import get_settings
from domain.proposal import ExecutiveInvestmentReport, InvestmentProposal
from domain.reports import InvestmentThesis
from utils.logging import get_logger

logger = get_logger(__name__)


class LLMNarrativeService:
    """Generates CEO-grade narrative when OPENAI_API_KEY is configured."""

    async def enrich_thesis_summary(self, thesis: InvestmentThesis) -> str | None:
        settings = get_settings()
        if not settings.openai_api_key:
            return None
        agent_lines = [
            f"{r.agent_name}: score {r.score:+.1f}, {r.summary[:100]}"
            for r in thesis.agent_reports[:8]
        ]
        prompt = (
            f"Eres el director de inversiones de un family office. Redacta un executive summary "
            f"en español (max 120 palabras) para {thesis.ticker}. "
            f"Recomendación: {thesis.recommendation.value}, confianza {thesis.confidence:.0%}. "
            f"Evidencia agentes:\n" + "\n".join(agent_lines)
        )
        return await self._complete(prompt)

    async def enrich_proposal_report(self, proposal: InvestmentProposal) -> str | None:
        settings = get_settings()
        if not settings.openai_api_key:
            return None
        lines = [f"{a.ticker} {a.instrument.value} ${a.allocation_usd}" for a in proposal.allocations]
        prompt = (
            f"Redacta en español un memo CEO (max 150 palabras) para asignar ${proposal.budget:.0f}: "
            + ", ".join(lines)
            + f". Perfil {proposal.risk_profile.value}. Incluye riesgo principal y catalizador."
        )
        return await self._complete(prompt)

    async def _complete(self, prompt: str) -> str | None:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={
                        "model": settings.openai_model,
                        "messages": [
                            {"role": "system", "content": "Eres un CIO institucional. Sé conciso y accionable."},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 300,
                        "temperature": 0.4,
                    },
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("llm.narrative.failed", error=str(exc))
            return None

    def apply_to_executive_report(
        self, report: ExecutiveInvestmentReport, llm_text: str | None
    ) -> ExecutiveInvestmentReport:
        if not llm_text:
            return report
        report.narrative = f"{llm_text}\n\n---\n{report.narrative}"
        return report
