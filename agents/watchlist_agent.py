"""Watchlist monitoring agent."""

from agents.base import BaseAgent
from domain.entities import WatchlistItem
from domain.enums import EvidenceCategory
from domain.reports import AgentReport, Finding, Reference


class WatchlistAgent(BaseAgent):
    name = "watchlist_agent"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        watchlist: list[WatchlistItem] = kwargs.get("watchlist", [])
        ticker = ticker.upper()
        item = next((w for w in watchlist if w.ticker == ticker and w.active), None)

        findings: list[Finding] = []
        if item:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"{ticker} is on active watchlist since {item.added_at.date()}",
                    confidence=1.0,
                    references=[Reference(source="watchlist", data_point="added_at", value=str(item.added_at))],
                )
            )
            summary = f"{ticker} under active watchlist monitoring."
            score = 0.0
            confidence = 0.9
        else:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"{ticker} is not on the active watchlist",
                    confidence=1.0,
                    references=[],
                )
            )
            summary = f"{ticker} not in watchlist."
            score = 0.0
            confidence = 0.95

        return AgentReport(
            agent_name=self.name,
            ticker=ticker,
            score=score,
            confidence=confidence,
            findings=findings,
            risks=[],
            opportunities=[],
            references=[],
            raw_data={"on_watchlist": item is not None, "watchlist_size": len(watchlist)},
            summary=summary,
        )
