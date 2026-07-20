"""Discover companies and build investment proposal in one flow."""

from domain.discovery import DiscoveryProposalResult
from domain.entities import WatchlistItem
from domain.proposal import InstrumentType, RiskProfile
from domain.reports import InvestmentThesis
from services.analysis_service import AnalysisService
from services.capital_fit import capital_price_policy, discovery_themes_for_capital
from services.company_discovery_service import CompanyDiscoveryService
from services.investment_proposal_service import InvestmentProposalService
from services.watchlist_service import WatchlistService
from utils.logging import get_logger

logger = get_logger(__name__)


class DiscoveryProposalService:
    def __init__(
        self,
        discovery_service: CompanyDiscoveryService,
        analysis_service: AnalysisService,
        proposal_service: InvestmentProposalService,
        watchlist_service: WatchlistService | None = None,
    ) -> None:
        self._discovery = discovery_service
        self._analysis = analysis_service
        self._proposal = proposal_service
        self._watchlist = watchlist_service

    async def discover_and_propose(
        self,
        budget: float,
        themes: list[str] | None = None,
        max_candidates: int = 15,
        proposal_top: int = 4,
        exclude_tickers: list[str] | None = None,
        watchlist: list[WatchlistItem] | None = None,
        portfolio=None,
        risk_profile: str = "balanced",
        instrument_mode: str = "auto",
        add_to_watchlist: bool = True,
    ) -> DiscoveryProposalResult:
        policy = capital_price_policy(budget, target_positions=proposal_top)
        themes = discovery_themes_for_capital(policy, themes)

        report = await self._discovery.research(
            themes=themes,
            max_candidates=max_candidates,
            exclude_tickers=exclude_tickers,
            max_price=policy.max_share_price,
        )

        if not report.candidates and policy.max_share_price is not None:
            logger.info("discover.proposal.retry_without_price_cap", budget=budget)
            report = await self._discovery.research(
                themes=themes,
                max_candidates=max_candidates,
                exclude_tickers=exclude_tickers,
                max_price=None,
            )

        if not report.candidates:
            raise ValueError(
                "No se encontraron candidatos. Prueba otros temas o amplía la búsqueda."
            )

        top = report.candidates[:proposal_top]
        tickers = [c.ticker for c in top]

        theses: list[InvestmentThesis] = []
        for candidate in top:
            try:
                thesis = await self._analysis.analyze_ticker(
                    candidate.ticker,
                    portfolio=portfolio,
                    watchlist=watchlist,
                )
                theses.append(thesis)
            except Exception as exc:
                logger.warning("discover.proposal.analyze_failed", ticker=candidate.ticker, error=str(exc))

        if not theses:
            raise ValueError("No se pudo analizar ningún candidato descubierto.")

        proposal = await self._proposal.build_proposal(
            budget=budget,
            theses=theses,
            instrument_mode=InstrumentType(instrument_mode),
            risk_profile=RiskProfile(risk_profile),
            tickers_filter=tickers,
            prefer_affordable=True,
        )

        added: list[str] = []
        if add_to_watchlist and self._watchlist:
            for t in tickers:
                try:
                    await self._watchlist.add(t, notes="Descubierto automáticamente")
                    added.append(t)
                except Exception as exc:
                    logger.warning("discover.proposal.watchlist_failed", ticker=t, error=str(exc))

        summary = (
            f"{policy.description_es} "
            f"Descubiertos {len(report.candidates)} candidatos; propuesta con {len(proposal.allocations)} "
            f"posiciones sobre ${budget:,.0f}: {', '.join(tickers)}."
        )
        if added:
            summary += f" Agregados a watchlist: {', '.join(added)}."

        return DiscoveryProposalResult(
            discovery=report,
            tickers_selected=tickers,
            proposal=proposal,
            watchlist_added=added,
            summary=summary,
        )
