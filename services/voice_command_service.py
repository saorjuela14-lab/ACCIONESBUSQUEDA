"""Parse Spanish voice/text commands and execute dashboard actions."""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.repositories.alert_repository import AlertRepository
from database.repositories.daily_trade_repository import DailyTradeRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.report_repository import ReportRepository
from database.repositories.watchlist_repository import WatchlistRepository
from database.repositories.watchlist_snapshot_repository import WatchlistSnapshotRepository
from domain.voice import VoiceCommandResult, VoiceHelpItem
from providers.discovery.ticker_extractor import extract_tickers
from providers.market.factory import get_market_provider
from providers.news.factory import get_news_provider
from services.alert_service import AlertService
from services.company_discovery_service import CompanyDiscoveryService
from services.daily_trade_recommendation_service import DailyTradeRecommendationService
from services.market_dashboard_service import MarketDashboardService
from services.watchlist_monitor_service import WatchlistMonitorService
from services.watchlist_service import WatchlistService
from utils.logging import get_logger

logger = get_logger(__name__)

_TICKER_ALIASES: dict[str, str] = {
    "apple": "AAPL",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "amd": "AMD",
    "intel": "INTC",
    "palantir": "PLTR",
    "coinbase": "COIN",
    "rocket": "RKLB",
    "oklo": "OKLO",
    "ionq": "IONQ",
    "vertex": "VRTX",
    "moderna": "MRNA",
    "pfizer": "PFE",
}

_HELP_ITEMS = [
    VoiceHelpItem(phrase="¿Cómo está el mercado?", description="Resumen del régimen y briefing"),
    VoiceHelpItem(phrase="Analiza NVDA", description="Análisis completo del comité"),
    VoiceHelpItem(phrase="Escanea la watchlist", description="Busca alertas en tu lista"),
    VoiceHelpItem(phrase="Recomendaciones del día", description="Trades de corto plazo"),
    VoiceHelpItem(phrase="Agrega RKLB a watchlist", description="Añadir ticker"),
    VoiceHelpItem(phrase="Descubre biotech", description="Investigar oportunidades"),
    VoiceHelpItem(phrase="Mis alertas", description="Alertas activas"),
    VoiceHelpItem(phrase="Mi portafolio", description="Estado del portafolio"),
]


class VoiceCommandService:
    async def handle(
        self,
        text: str,
        session: AsyncSession,
        portfolio_id: str | None = None,
    ) -> VoiceCommandResult:
        raw = (text or "").strip()
        if not raw:
            return VoiceCommandResult(
                intent="unknown",
                success=False,
                speech="No escuché nada. Prueba decir: cómo está el mercado, o analiza un ticker.",
            )

        normalized = self._normalize(raw)
        intent, params = self._parse_intent(normalized, raw)

        logger.info("voice.command", intent=intent, params=params, text=raw[:120])

        handlers = {
            "help": self._help,
            "market": self._market_summary,
            "analyze": self._analyze,
            "scan_watchlist": self._scan_watchlist,
            "daily_trades": self._daily_trades,
            "watchlist_list": self._watchlist_list,
            "watchlist_add": self._watchlist_add,
            "watchlist_remove": self._watchlist_remove,
            "alerts": self._alerts,
            "portfolio": self._portfolio_summary,
            "discovery": self._discovery,
            "refresh": self._refresh,
        }

        handler = handlers.get(intent)
        if not handler:
            return VoiceCommandResult(
                intent="unknown",
                success=False,
                speech=(
                    "No entendí ese comando. Di ayuda para ver ejemplos, "
                    "o prueba: cómo está el mercado, analiza VRT, escanea watchlist."
                ),
            )

        try:
            return await handler(session, params, portfolio_id)
        except Exception as exc:
            logger.warning("voice.handler_failed", intent=intent, error=str(exc))
            return VoiceCommandResult(
                intent=intent,
                success=False,
                speech=f"No pude completar {intent}: {exc}",
            )

    def _normalize(self, text: str) -> str:
        lowered = text.lower().strip()
        nfkd = unicodedata.normalize("NFKD", lowered)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    def _parse_intent(self, norm: str, raw: str) -> tuple[str, dict]:
        if re.search(r"\b(ayuda|comandos|que puedes hacer|qué puedes hacer|help)\b", norm):
            return "help", {}

        if re.search(
            r"\b(mercado|briefing|regimen|como esta el mercado|como va el mercado|"
            r"resumen del mercado|panorama)\b",
            norm,
        ):
            return "market", {}

        if re.search(r"\b(actualiza|refresca|recarga)\b", norm):
            return "refresh", {}

        if re.search(r"\b(recomendaciones|trades|corto plazo|operaciones del dia)\b", norm):
            return "daily_trades", {}

        if re.search(r"\b(escanea|escanear|scan)\b.*\b(watchlist|lista)\b", norm) or norm in (
            "escanea watchlist",
            "escanear watchlist",
        ):
            return "scan_watchlist", {}

        if re.search(r"\b(alertas|alarmas)\b", norm):
            return "alerts", {}

        if re.search(r"\b(portafolio|portfolio|cartera)\b", norm):
            return "portfolio", {}

        m = re.search(
            r"\b(?:anali[zs]a|analizar|revisa|revisar|opinion|opinión sobre)\s+(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "analyze", {"ticker": ticker}

        m = re.search(r"\b(?:agrega|añade|anade|add|pon)\s+(.+?)\s+(?:a|en|al)\s+watchlist\b", norm)
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "watchlist_add", {"ticker": ticker}

        m = re.search(r"\b(?:quita|elimina|borra|remove)\s+(.+?)\s+(?:de|del)\s+watchlist\b", norm)
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "watchlist_remove", {"ticker": ticker}

        if re.search(r"\b(watchlist|lista de seguimiento|mis tickers)\b", norm):
            return "watchlist_list", {}

        m = re.search(r"\b(descubre|descubrir|investiga|investigar|busca oportunidades en)\s+(.+)$", norm)
        if m:
            theme = m.group(2).strip(" .,!?:;")
            return "discovery", {"theme": theme}

        # Fallback: bare ticker mention "NVDA" or "analiza apple"
        tickers = extract_tickers(raw.upper())
        if tickers and re.search(r"\b(analiza|analizar|revisa)\b", norm):
            return "analyze", {"ticker": tickers[0]}

        return "unknown", {}

    def _resolve_ticker(self, fragment: str, raw: str) -> str | None:
        frag = fragment.strip(" .,!?:;")
        norm_frag = self._normalize(frag)
        if norm_frag in _TICKER_ALIASES:
            return _TICKER_ALIASES[norm_frag]
        tickers = extract_tickers(frag.upper()) or extract_tickers(raw.upper())
        if tickers:
            return tickers[0]
        word = frag.split()[0].upper() if frag else ""
        if 1 < len(word) <= 5 and word.isalpha():
            return word
        return None

    async def _help(self, session, params, portfolio_id) -> VoiceCommandResult:
        lines = [f"Di: {h.phrase}. {h.description}." for h in _HELP_ITEMS[:6]]
        return VoiceCommandResult(
            intent="help",
            speech="Puedo hablarte del mercado, analizar tickers, escanear watchlist y más. "
            + " ".join(lines),
            data={"commands": [h.model_dump() for h in _HELP_ITEMS]},
        )

    async def _market_summary(self, session, params, portfolio_id) -> VoiceCommandResult:
        overview = await MarketDashboardService().build()

        regime_es = {"bullish": "alcista", "bearish": "bajista", "neutral": "neutral"}.get(
            overview.market_regime, overview.market_regime
        )
        parts = [
            f"El mercado se ve {regime_es}, con puntuación {overview.market_regime_score:.1f}.",
        ]
        if overview.indices:
            top = overview.indices[:3]
            idx_txt = ", ".join(
                f"{i.name} {i.change_pct:+.1f}%" if i.change_pct is not None else i.name
                for i in top
            )
            parts.append(f"Índices: {idx_txt}.")

        report = await ReportRepository(session).get_latest_daily_report()
        if report and report.market_report:
            summary = (report.market_report.market_summary or "")[:400]
            if summary:
                parts.append(summary)

        return VoiceCommandResult(
            intent="market",
            speech=" ".join(parts),
            data={"regime": overview.market_regime, "score": overview.market_regime_score},
            ui_action="refresh",
        )

    async def _analyze(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        return VoiceCommandResult(
            intent="analyze",
            speech=f"Analizando {ticker} con el comité de inversión. Esto puede tardar un minuto.",
            params={"ticker": ticker},
            ui_action=f"analyze:{ticker}",
        )

    async def _scan_watchlist(self, session, params, portfolio_id) -> VoiceCommandResult:
        settings = get_settings()
        monitor = WatchlistMonitorService(
            WatchlistRepository(session),
            WatchlistSnapshotRepository(session),
            AlertService(AlertRepository(session), settings.alert_cooldown_hours),
            get_market_provider(),
            get_news_provider(),
        )
        result = await monitor.scan_all()
        alerts = result.get("alerts", 0)
        scanned = result.get("scanned", 0)
        speech = f"Escaneé {scanned} tickers de tu watchlist."
        if alerts:
            speech += f" Detecté {alerts} alertas nuevas. Revisa el panel."
        else:
            speech += " No hay alertas nuevas por ahora."
        return VoiceCommandResult(
            intent="scan_watchlist",
            speech=speech,
            data=result,
            ui_action="refresh",
        )

    async def _daily_trades(self, session, params, portfolio_id) -> VoiceCommandResult:
        market = get_market_provider()
        svc = DailyTradeRecommendationService(
            market_provider=market,
            discovery_service=CompanyDiscoveryService(market_provider=market),
            trade_repo=DailyTradeRepository(session),
        )
        report = await svc.get_latest()
        if not report or not report.picks:
            report = await svc.generate(session="pre_market", persist=True)

        if not report.picks:
            return VoiceCommandResult(
                intent="daily_trades",
                success=False,
                speech="No hay recomendaciones de corto plazo disponibles ahora mismo.",
            )

        picks_txt = ". ".join(
            f"{p.ticker} {p.action}, objetivo ${p.target_price or 'N/D'}"
            for p in report.picks[:4]
        )
        speech = (
            f"Recomendaciones de corto plazo, régimen {report.market_regime or 'N/D'}. "
            f"{picks_txt}."
        )
        return VoiceCommandResult(
            intent="daily_trades",
            speech=speech,
            data={"picks": [p.model_dump() for p in report.picks[:8]]},
            ui_action="scroll:trade-recs-panel",
        )

    async def _watchlist_list(self, session, params, portfolio_id) -> VoiceCommandResult:
        items = await WatchlistRepository(session).list_active()
        if not items:
            return VoiceCommandResult(
                intent="watchlist_list",
                speech="Tu watchlist está vacía. Di: agrega RKLB a watchlist.",
            )
        tickers = ", ".join(w.ticker for w in items[:12])
        extra = f" y {len(items) - 12} más" if len(items) > 12 else ""
        return VoiceCommandResult(
            intent="watchlist_list",
            speech=f"Tienes {len(items)} tickers en watchlist: {tickers}{extra}.",
            data={"tickers": [w.ticker for w in items]},
        )

    async def _watchlist_add(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        market = get_market_provider()
        try:
            quote = await market.get_quote(ticker)
            if not quote.get("current_price") and quote.get("company_name", ticker).upper() == ticker:
                return VoiceCommandResult(
                    intent="watchlist_add",
                    success=False,
                    speech=f"No encontré el ticker {ticker}. ¿Puedes repetirlo?",
                )
        except Exception:
            return VoiceCommandResult(
                intent="watchlist_add",
                success=False,
                speech=f"No pude validar {ticker}. Verifica el símbolo.",
            )

        await WatchlistService(WatchlistRepository(session), market).add(ticker, notes="Agregado por voz")
        name = quote.get("company_name") or ticker
        return VoiceCommandResult(
            intent="watchlist_add",
            speech=f"Listo, agregué {ticker}, {name}, a tu watchlist.",
            params={"ticker": ticker},
            ui_action="refresh",
        )

    async def _watchlist_remove(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        removed = await WatchlistService(
            WatchlistRepository(session), get_market_provider()
        ).remove(ticker)
        if not removed:
            return VoiceCommandResult(
                intent="watchlist_remove",
                success=False,
                speech=f"{ticker} no estaba en tu watchlist.",
            )
        return VoiceCommandResult(
            intent="watchlist_remove",
            speech=f"Eliminé {ticker} de la watchlist.",
            params={"ticker": ticker},
            ui_action="refresh",
        )

    async def _alerts(self, session, params, portfolio_id) -> VoiceCommandResult:
        alerts = await AlertRepository(session).list_unacknowledged(8)
        if not alerts:
            return VoiceCommandResult(
                intent="alerts",
                speech="No tienes alertas activas en este momento.",
            )
        lines = [f"{a.ticker}: {a.title}" for a in alerts[:5]]
        speech = f"Tienes {len(alerts)} alertas. " + ". ".join(lines) + "."
        return VoiceCommandResult(
            intent="alerts",
            speech=speech,
            data={"count": len(alerts)},
        )

    async def _portfolio_summary(self, session, params, portfolio_id) -> VoiceCommandResult:
        portfolios = await PortfolioRepository(session).list_all()
        if not portfolios:
            return VoiceCommandResult(
                intent="portfolio",
                speech="Aún no tienes portafolio. Créalo desde el panel en la sección Portafolio.",
            )
        p = sorted(portfolios, key=lambda x: x.updated_at, reverse=True)[0]
        if portfolio_id:
            match = next((x for x in portfolios if x.id == portfolio_id), None)
            if match:
                p = match

        from services.portfolio_service import PortfolioService

        svc = PortfolioService(PortfolioRepository(session), get_market_provider())
        try:
            p = await svc.refresh_prices(p.id)
            ret = p.return_pct
        except Exception:
            ret = 0

        mode_val = getattr(p.mode, "value", p.mode) if hasattr(p, "mode") else "real"
        mode = "demo" if mode_val == "demo" else "real"
        speech = (
            f"Portafolio {p.name}, modo {mode}. "
            f"Capital inicial ${p.initial_capital:,.0f}, valor actual ${p.total_value:,.0f}. "
            f"Rendimiento {ret:+.1f} por ciento."
        )
        return VoiceCommandResult(
            intent="portfolio",
            speech=speech,
            data={"portfolio_id": p.id, "return_pct": ret},
        )

    async def _discovery(self, session, params, portfolio_id) -> VoiceCommandResult:
        theme = params.get("theme", "growth stocks")
        return VoiceCommandResult(
            intent="discovery",
            speech=f"Investigando oportunidades en {theme}. Abro descubrimiento en el panel.",
            params={"theme": theme},
            ui_action=f"discovery:{theme}",
        )

    async def _refresh(self, session, params, portfolio_id) -> VoiceCommandResult:
        return VoiceCommandResult(
            intent="refresh",
            speech="Actualizando el panel.",
            ui_action="refresh",
        )
