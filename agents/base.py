"""Base agent contract for the investment committee."""

from abc import ABC, abstractmethod

from domain.reports import AgentReport


class BaseAgent(ABC):
    """All agents deliver structured evidence; none make buy/sell decisions."""

    name: str = "base_agent"

    @abstractmethod
    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        raise NotImplementedError

    def _clamp_score(self, value: float) -> float:
        return max(-100.0, min(100.0, value))

    def _clamp_confidence(self, value: float) -> float:
        return max(0.0, min(1.0, value))
