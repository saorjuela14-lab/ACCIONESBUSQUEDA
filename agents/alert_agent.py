"""Alert generation agent - emits only high-relevance signals."""

from agents.base import BaseAgent
from domain.entities import Alert
from domain.enums import AlertSeverity, AlertType, EvidenceCategory
from domain.reports import AgentReport, Finding, Reference


class AlertAgent(BaseAgent):
    name = "alert_agent"

    _THRESHOLDS = {
        "breakout_score": 25,
        "breakdown_score": -25,
        "sentiment_shift": 20,
    }

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        technical_report = kwargs.get("technical_report")
        sentiment_report = kwargs.get("sentiment_report")
        news_report = kwargs.get("news_report")

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        alerts: list[Alert] = []
        score = 0.0

        if technical_report:
            tech_score = technical_report.score
            if tech_score >= self._THRESHOLDS["breakout_score"]:
                alert = Alert(
                    ticker=ticker.upper(),
                    alert_type=AlertType.BREAKOUT,
                    severity=AlertSeverity.HIGH,
                    title=f"{ticker} technical breakout signal",
                    description=f"Technical score {tech_score:.1f} exceeds breakout threshold",
                )
                alerts.append(alert)
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=alert.description,
                        confidence=technical_report.confidence,
                        references=technical_report.references[:2],
                    )
                )
            elif tech_score <= self._THRESHOLDS["breakdown_score"]:
                alert = Alert(
                    ticker=ticker.upper(),
                    alert_type=AlertType.BREAKDOWN,
                    severity=AlertSeverity.HIGH,
                    title=f"{ticker} technical breakdown signal",
                    description=f"Technical score {tech_score:.1f} below breakdown threshold",
                )
                alerts.append(alert)
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=alert.description,
                        confidence=technical_report.confidence,
                        references=technical_report.references[:2],
                    )
                )

        if sentiment_report and abs(sentiment_report.score) >= self._THRESHOLDS["sentiment_shift"]:
            alert = Alert(
                ticker=ticker.upper(),
                alert_type=AlertType.SENTIMENT_SHIFT,
                severity=AlertSeverity.MEDIUM,
                title=f"{ticker} sentiment shift detected",
                description=f"Sentiment score {sentiment_report.score:.1f}",
            )
            alerts.append(alert)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=alert.description,
                    confidence=sentiment_report.confidence,
                    references=[Reference(source="alert_engine", data_point="sentiment_score", value=sentiment_report.score)],
                )
            )

        if news_report and len(news_report.risks) >= 3:
            alert = Alert(
                ticker=ticker.upper(),
                alert_type=AlertType.REGULATORY_NEWS,
                severity=AlertSeverity.HIGH,
                title=f"{ticker} elevated negative news flow",
                description=f"{len(news_report.risks)} bearish news items detected",
            )
            alerts.append(alert)
            risks.extend(news_report.risks[:2])

        kwargs["generated_alerts"] = alerts

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=0.75 if alerts else 0.5,
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=[],
            raw_data={
                "alerts": [a.model_dump(mode="json") for a in alerts],
                "alerts_generated": len(alerts),
                "alert_types": [a.alert_type.value for a in alerts],
            },
            summary=f"Generadas {len(alerts)} alertas relevantes (política anti-spam aplicada).",
        )
