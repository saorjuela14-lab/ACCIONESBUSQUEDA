"""Investment committee orchestration service."""

import asyncio

from agents.alert_agent import AlertAgent
from agents.company_risk_agent import CompanyRiskAgent
from agents.corporate_actions_agent import CorporateActionsAgent
from agents.country_risk_agent import CountryRiskAgent
from agents.fundamental_agent import FundamentalAgent
from agents.investment_director import InvestmentDirector
from agents.investment_memory import InvestmentMemoryAgent
from agents.macro_agent import MacroAgent
from agents.market_dependency_agent import MarketDependencyAgent
from agents.news_agent import NewsAgent
from agents.portfolio_agent import PortfolioAgent
from agents.sentiment_agent import SentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.valuation_agent import ValuationAgent
from agents.watchlist_agent import WatchlistAgent
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from domain.entities import Portfolio
from domain.reports import AgentReport, InvestmentThesis
from providers.interfaces import MacroProvider, MarketDataProvider, NewsProvider, SentimentProvider
from providers.sentiment.factory import get_sentiment_provider
from services.alert_service import AlertService
from utils.logging import get_logger

logger = get_logger(__name__)


class AnalysisService:
    """Orchestrates the multi-agent investment committee pipeline."""

    def __init__(
        self,
        market_provider: MarketDataProvider,
        news_provider: NewsProvider,
        macro_provider: MacroProvider,
        alert_repo: AlertRepository,
        memory_repo: InvestmentMemoryRepository,
        sentiment_provider: SentimentProvider | None = None,
        max_concentration_pct: float = 25.0,
    ) -> None:
        self._market = market_provider
        self._fundamental = FundamentalAgent(market_provider)
        self._technical = TechnicalAgent(market_provider)
        self._macro = MacroAgent(macro_provider, market_provider)
        self._news = NewsAgent(news_provider)
        self._sentiment = SentimentAgent()
        self._valuation = ValuationAgent(market_provider)
        self._country_risk = CountryRiskAgent(market_provider, news_provider)
        self._company_risk = CompanyRiskAgent(news_provider)
        self._corporate_actions = CorporateActionsAgent(market_provider, news_provider)
        self._market_dependency = MarketDependencyAgent(market_provider)
        self._portfolio = PortfolioAgent(market_provider, max_concentration_pct)
        self._watchlist = WatchlistAgent()
        self._alert = AlertAgent()
        self._director = InvestmentDirector()
        self._memory = InvestmentMemoryAgent(memory_repo)
        self._alert_repo = alert_repo
        self._memory_repo = memory_repo

    async def analyze_ticker(
        self,
        ticker: str,
        portfolio: Portfolio | None = None,
        watchlist: list | None = None,
    ) -> InvestmentThesis:
        ticker = ticker.upper()
        quote = await self._market.get_quote(ticker)
        company_name = quote.get("company_name", ticker)

        logger.info("analysis.start", ticker=ticker)

        context_agents = [
            self._fundamental,
            self._macro,
            self._news,
            self._sentiment,
            self._valuation,
            self._country_risk,
            self._company_risk,
            self._corporate_actions,
            self._market_dependency,
        ]

        common_kwargs = {
            "company_name": company_name,
            "sector": quote.get("sector"),
            "industry": quote.get("industry"),
        }

        prior_reports: list[AgentReport] = list(
            await asyncio.gather(
                *[agent.analyze(ticker, **common_kwargs) for agent in context_agents]
            )
        )

        technical_report = await self._technical.analyze(
            ticker,
            **common_kwargs,
            prior_reports=prior_reports,
        )

        reports: list[AgentReport] = prior_reports + [technical_report]

        # Prior Investment Memory as committee evidence (not only post-store)
        memory_context = await self._prior_memory_report(ticker)
        if memory_context:
            reports.append(memory_context)

        portfolio_report = await self._portfolio.analyze(ticker, portfolio=portfolio)
        watchlist_report = await self._watchlist.analyze(ticker, watchlist=watchlist or [])
        reports.extend([portfolio_report, watchlist_report])

        technical_report = next(r for r in reports if r.agent_name == "technical_agent")
        sentiment_report = next(r for r in prior_reports if r.agent_name == "sentiment_agent")
        news_report = next(r for r in prior_reports if r.agent_name == "news_agent")

        alert_report = await self._alert.analyze(
            ticker,
            technical_report=technical_report,
            sentiment_report=sentiment_report,
            news_report=news_report,
        )
        reports.append(alert_report)

        from domain.entities import Alert

        alert_svc = AlertService(self._alert_repo)
        for alert_data in alert_report.raw_data.get("alerts", []):
            await alert_svc.emit(Alert(**alert_data))

        weights = await self._memory_repo.get_agent_weights()
        if not weights:
            weights = InvestmentDirector.DEFAULT_WEIGHTS

        thesis = self._director.build_thesis(ticker, reports, weights, float(quote.get("current_price") or 0))

        await self._memory.analyze(
            ticker,
            thesis=thesis,
            entry_price=float(quote.get("current_price") or 0) or None,
        )

        # Thesis flip → invalidate mandate / trigger lifecycle exit when held
        await self._maybe_invalidate_on_sell(ticker, thesis, portfolio)

        for alert in alert_report.raw_data.get("alert_types", []):
            logger.info("alert.generated", ticker=ticker, type=alert)

        logger.info("analysis.complete", ticker=ticker, recommendation=thesis.recommendation.value)
        return thesis

    async def _prior_memory_report(self, ticker: str) -> AgentReport | None:
        try:
            latest = await self._memory_repo.latest_by_ticker([ticker.upper()])
            record = latest.get(ticker.upper())
            if not record:
                return None
            from domain.enums import EvidenceCategory
            from domain.reports import Finding

            score = 0.0
            rec = (record.recommendation or "").lower()
            if rec in ("buy", "strong_buy"):
                score = 15.0
            elif rec in ("sell", "strong_sell"):
                score = -20.0
            findings = [
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=(
                        f"Memoria previa: {record.recommendation} "
                        f"(conf {record.confidence:.0%}). "
                        f"{(record.thesis or '')[:160]}"
                    ),
                    confidence=min(0.9, max(0.4, record.confidence or 0.5)),
                    references=[],
                )
            ]
            if record.was_correct is True:
                findings.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Evaluación histórica: tesis previa acertada.",
                        confidence=0.8,
                        references=[],
                    )
                )
                score += 5
            elif record.was_correct is False:
                findings.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement="Evaluación histórica: tesis previa incorrecta — cautela.",
                        confidence=0.85,
                        references=[],
                    )
                )
                score -= 10
            return AgentReport(
                agent_name="investment_memory",
                ticker=ticker.upper(),
                score=score,
                confidence=record.confidence or 0.6,
                findings=findings,
                risks=[],
                opportunities=[],
                references=[],
                raw_data={"prior_recommendation": record.recommendation, "record_id": record.id},
                summary=f"Memoria: {record.recommendation} · {(record.expected_outcome or '')[:80]}",
            )
        except Exception as exc:
            logger.warning("analysis.memory_context_failed", error=str(exc))
            return None

    async def _maybe_invalidate_on_sell(
        self,
        ticker: str,
        thesis: InvestmentThesis,
        portfolio: Portfolio | None,
    ) -> None:
        rec = thesis.recommendation.value if thesis.recommendation else ""
        if rec not in ("sell", "strong_sell"):
            return
        # Only act if held in NexBuy book or Alpaca
        held = False
        if portfolio and any(p.ticker.upper() == ticker.upper() for p in portfolio.positions):
            held = True
        if not held:
            try:
                from services.alpaca_order_service import AlpacaOrderService

                broker = AlpacaOrderService()
                if broker.is_configured():
                    positions = await broker.get_positions()
                    held = any(p.symbol.upper() == ticker.upper() for p in positions)
            except Exception:
                held = False
        if not held:
            return

        try:
            from database.engine import get_session
            from services.position_lifecycle_service import PositionLifecycleService

            reason = (
                f"Comité: {rec.upper()} — {(thesis.executive_summary or thesis.investment_thesis or '')[:180]}"
            )
            # Prefer current request session via memory repo's session if available
            session = getattr(self._memory_repo, "_session", None)
            if session is not None:
                life = PositionLifecycleService(session)
                await life.invalidate_thesis(ticker, reason)
                from config.settings import get_settings

                if get_settings().lifecycle_auto_exit:
                    await life.scan(execute_exits=True)
                logger.info("analysis.thesis_flip_exit", ticker=ticker, recommendation=rec)
                return
            async for s in get_session():
                life = PositionLifecycleService(s)
                await life.invalidate_thesis(ticker, reason)
                from config.settings import get_settings

                if get_settings().lifecycle_auto_exit:
                    await life.scan(execute_exits=True)
                break
        except Exception as exc:
            logger.warning("analysis.thesis_flip_failed", ticker=ticker, error=str(exc))

    async def get_agent_reports(self, ticker: str) -> list[AgentReport]:
        thesis = await self.analyze_ticker(ticker)
        return thesis.agent_reports
