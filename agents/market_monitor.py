"""Market monitor for scheduled session reports."""

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, MarketSession, ReportType
from domain.reports import AgentReport, Finding, MarketReport, Reference
from providers.interfaces import MacroProvider, MarketDataProvider


class MarketMonitor(BaseAgent):
    name = "market_monitor"

    _BENCHMARKS = ["^GSPC", "^IXIC", "^DJI", "XLK", "XLF", "XLE", "XLV"]

    def __init__(self, market_provider: MarketDataProvider, macro_provider: MacroProvider) -> None:
        self._market = market_provider
        self._macro = macro_provider

    async def analyze(self, ticker: str = "SPY", **kwargs) -> AgentReport:
        session: MarketSession = kwargs.get("session", MarketSession.MID_SESSION)
        report = await self.generate_market_report(session)

        return AgentReport(
            agent_name=self.name,
            ticker=None,
            score=0.0,
            confidence=0.8,
            findings=[
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=report.market_summary,
                    confidence=0.85,
                    references=[Reference(source="market_monitor", data_point="session", value=session.value)],
                )
            ],
            risks=[],
            opportunities=[],
            references=[],
            raw_data={"market_report": report.model_dump(mode="json")},
            summary=report.market_summary,
        )

    async def generate_market_report(self, session: MarketSession) -> MarketReport:
        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)

        strong: list[str] = []
        weak: list[str] = []
        volume_leaders: list[str] = []
        volatile: list[str] = []
        findings: list[Finding] = []

        for symbol in self._BENCHMARKS:
            try:
                hist = await self._market.get_history(symbol, period="5d", interval="1d")
                if hist.empty or len(hist) < 2:
                    continue
                change = ((hist["Close"].iloc[-1] / hist["Close"].iloc[-2]) - 1) * 100
                vol = hist["Volume"].iloc[-1]
                daily_range = ((hist["High"].iloc[-1] - hist["Low"].iloc[-1]) / hist["Close"].iloc[-1]) * 100

                label = symbol.replace("^", "")
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=f"{label}: {change:+.2f}% (range {daily_range:.2f}%)",
                        confidence=0.95,
                        references=[Reference(source="yfinance", data_point=symbol, value=round(change, 2))],
                    )
                )

                if change > 0.5:
                    strong.append(label)
                elif change < -0.5:
                    weak.append(label)
                volume_leaders.append(f"{label}:{vol:,.0f}")
                volatile.append(f"{label}:{daily_range:.2f}%")
            except Exception:
                continue

        macro = await self._macro.get_macro_snapshot()
        macro_events = []
        for name, data in macro.get("indicators", {}).items():
            chg = data.get("change_pct")
            chg_txt = f" ({chg:+.2f}%)" if chg is not None else ""
            macro_events.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"{name}: {data.get('value')}{chg_txt}",
                    confidence=0.9,
                    references=[Reference(source="yfinance", data_point=name, value=data.get("value"))],
                )
            )

        report_type_map = {
            MarketSession.PRE_MARKET: ReportType.PRE_MARKET,
            MarketSession.MID_SESSION: ReportType.MID_SESSION,
            MarketSession.POWER_HOUR: ReportType.POWER_HOUR,
            MarketSession.POST_MARKET: ReportType.POST_MARKET,
        }

        return MarketReport(
            report_type=report_type_map.get(session, ReportType.MID_SESSION),
            session=session,
            market_summary=f"{session.value.replace('_', ' ').title()} report at {now.strftime('%H:%M ET')}",
            strong_sectors=strong[:5],
            weak_sectors=weak[:5],
            highest_volume=volume_leaders[:5],
            highest_volatility=sorted(volatile, key=lambda x: float(x.split(":")[1].replace("%", "")), reverse=True)[:5],
            macro_events=macro_events,
            technical_changes=findings[:5],
            alerts=[],
        )
