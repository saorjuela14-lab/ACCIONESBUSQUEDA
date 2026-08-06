"""Parse Spanish voice/text commands and execute dashboard actions."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.repositories.alert_repository import AlertRepository
from database.repositories.daily_trade_repository import DailyTradeRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.report_repository import ReportRepository
from database.repositories.watchlist_repository import WatchlistRepository
from database.repositories.watchlist_snapshot_repository import WatchlistSnapshotRepository
from domain.broker import ExecuteLine, ExecuteOrdersRequest
from domain.voice import VoiceCommandResult, VoiceHelpItem
from providers.discovery.ticker_extractor import extract_tickers
from providers.market.factory import get_market_provider
from providers.news.factory import get_news_provider
from services.alert_service import AlertService
from services.alpaca_order_service import AlpacaOrderService
from services.company_discovery_service import CompanyDiscoveryService
from services.daily_trade_recommendation_service import DailyTradeRecommendationService
from services.market_dashboard_service import MarketDashboardService
from services.technical_chart_service import TechnicalChartService
from services.watchlist_monitor_service import WatchlistMonitorService
from services.watchlist_service import WatchlistService
from utils.logging import get_logger

logger = get_logger(__name__)

_PENDING_TTL_S = 120.0
_PENDING: dict[str, dict[str, Any]] = {}


def _pending_key(portfolio_id: str | None) -> str:
    return (portfolio_id or "").strip() or "_default"


def _set_pending(portfolio_id: str | None, action: dict[str, Any]) -> None:
    key = _pending_key(portfolio_id)
    payload = dict(action)
    payload["_expires_at"] = time.time() + _PENDING_TTL_S
    payload["portfolio_id"] = portfolio_id
    _PENDING[key] = payload


def _get_pending(portfolio_id: str | None) -> dict[str, Any] | None:
    key = _pending_key(portfolio_id)
    item = _PENDING.get(key)
    if not item:
        return None
    if float(item.get("_expires_at", 0)) < time.time():
        _PENDING.pop(key, None)
        return None
    return item


def _clear_pending(portfolio_id: str | None) -> None:
    _PENDING.pop(_pending_key(portfolio_id), None)


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
    "netflix": "NFLX",
    "disney": "DIS",
    "boeing": "BA",
    "starbucks": "SBUX",
    "coca cola": "KO",
    "cocacola": "KO",
}

_HELP_ITEMS = [
    VoiceHelpItem(phrase="¿Cómo está el mercado?", description="Resumen del régimen, sentimiento y briefing"),
    VoiceHelpItem(phrase="Precio de NVDA", description="Cotización y cambio de 5 días"),
    VoiceHelpItem(phrase="Analiza NVDA", description="Análisis completo del comité"),
    VoiceHelpItem(phrase="Técnico de AAPL", description="Playbook técnico y sesgo del gráfico"),
    VoiceHelpItem(phrase="Compra 1 AAPL", description="Previsualiza compra Alpaca (luego confirma)"),
    VoiceHelpItem(phrase="Vende todo TSLA", description="Previsualiza cierre o venta parcial"),
    VoiceHelpItem(phrase="Confirma", description="Ejecuta la orden pendiente"),
    VoiceHelpItem(phrase="Mis posiciones", description="Posiciones abiertas en Alpaca"),
    VoiceHelpItem(phrase="Agrega RKLB a watchlist", description="Añadir ticker a la lista"),
    VoiceHelpItem(phrase="Descubre biotech", description="Investigar oportunidades por tema"),
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
            "app_help": self._app_help,
            "market": self._market_summary,
            "quote": self._quote,
            "technical": self._technical,
            "analyze": self._analyze,
            "scan_watchlist": self._scan_watchlist,
            "daily_trades": self._daily_trades,
            "watchlist_list": self._watchlist_list,
            "watchlist_add": self._watchlist_add,
            "watchlist_remove": self._watchlist_remove,
            "alerts": self._alerts,
            "portfolio": self._portfolio_summary,
            "broker": self._broker_status,
            "positions": self._positions,
            "buy": self._buy_preview,
            "sell": self._sell_preview,
            "confirm": self._confirm,
            "cancel_pending": self._cancel_pending,
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
                    "o prueba: cómo está el mercado, precio de AAPL, compra 1 NVDA, "
                    "técnico de TSLA, escanea watchlist."
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
        compact = re.sub(r"\s+", " ", norm).strip(" .,!?:;")

        if re.search(
            r"\b(como funciona( la)? (app|panel|nexbuy|monarch)|que es (nexbuy|monarch( capital)?)|"
            r"ayuda (de |del )?(la )?(app|panel|nexbuy|monarch)|app help)\b",
            norm,
        ):
            return "app_help", {}

        if re.search(r"\b(ayuda|comandos|que puedes hacer|qué puedes hacer|help)\b", norm):
            return "help", {}

        # Confirm / cancel — before buy/sell so "confirma compra" still confirms
        if re.search(r"\b(confirma|confirmar|ejecuta|ejecutar|adelante)\b", norm):
            return "confirm", {}
        if re.fullmatch(r"(si|ok|dale|vale|yes)", compact):
            return "confirm", {}

        if re.search(
            r"\b(cancela|cancelar|anula|anular|olvidalo|dejalo|no gracias|"
            r"cancel pending|descarta)\b",
            norm,
        ):
            return "cancel_pending", {}

        # Buy: "compra 1 aapl" / "compra aapl 2 acciones" / "compra apple"
        m = re.search(
            r"\b(?:compra|comprar|buy)\s+(\d+(?:[.,]\d+)?)\s+(?:acciones?\s+(?:de\s+)?)?(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(2), raw)
            if ticker:
                return "buy", {"ticker": ticker, "shares": self._parse_qty(m.group(1))}

        m = re.search(
            r"\b(?:compra|comprar|buy)\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s*(?:acciones?)?$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "buy", {"ticker": ticker, "shares": self._parse_qty(m.group(2))}

        m = re.search(r"\b(?:compra|comprar|buy)\s+(.+)$", norm)
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "buy", {"ticker": ticker, "shares": 1.0}

        # Sell / close
        m = re.search(
            r"\b(?:vende|vender|sell|cierra|cerrar)\s+todo(?:\s+lo)?(?:\s+de)?\s+(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "sell", {"ticker": ticker, "close_all": True}

        m = re.search(
            r"\b(?:cierra|cerrar)\s+(?:la\s+)?(?:posicion|posición)(?:\s+de)?\s+(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "sell", {"ticker": ticker, "close_all": True}

        m = re.search(
            r"\b(?:vende|vender|sell)\s+(\d+(?:[.,]\d+)?)\s+(?:acciones?\s+(?:de\s+)?)?(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(2), raw)
            if ticker:
                return "sell", {
                    "ticker": ticker,
                    "shares": self._parse_qty(m.group(1)),
                    "close_all": False,
                }

        m = re.search(
            r"\b(?:vende|vender|sell)\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s*(?:acciones?)?$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "sell", {
                    "ticker": ticker,
                    "shares": self._parse_qty(m.group(2)),
                    "close_all": False,
                }

        m = re.search(r"\b(?:vende|vender|sell|cierra|cerrar)\s+(.+)$", norm)
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "sell", {"ticker": ticker, "close_all": True}

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

        if re.search(r"\b(escanea|escanear|scan)\b.*\b(watchlist|lista)\b", norm) or compact in (
            "escanea watchlist",
            "escanear watchlist",
        ):
            return "scan_watchlist", {}

        if re.search(r"\b(alertas|alarmas)\b", norm):
            return "alerts", {}

        if re.search(
            r"\b(posiciones|mis posiciones|posiciones abiertas|posiciones de alpaca)\b",
            norm,
        ):
            return "positions", {}

        if re.search(r"\b(broker|alpaca|estado del broker|cuenta alpaca|pulso alpaca)\b", norm):
            return "broker", {}

        if re.search(r"\b(portafolio|portfolio|cartera)\b", norm):
            return "portfolio", {}

        m = re.search(
            r"\b(?:precio(?:\s+de)?|cotizacion(?:\s+de)?|cotización(?:\s+de)?|"
            r"cuanto (?:vale|cuesta|esta)|quote)\s+(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "quote", {"ticker": ticker}

        m = re.search(
            r"\b(?:tecnic[oa](?:\s+de)?|analisis tecnico(?:\s+de)?|"
            r"análisis técnico(?:\s+de)?|playbook(?:\s+de)?|grafico(?:\s+de)?|"
            r"gráfico(?:\s+de)?)\s+(.+)$",
            norm,
        )
        if m:
            ticker = self._resolve_ticker(m.group(1), raw)
            if ticker:
                return "technical", {"ticker": ticker}

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

        tickers = extract_tickers(raw.upper())
        if tickers and re.search(r"\b(analiza|analizar|revisa)\b", norm):
            return "analyze", {"ticker": tickers[0]}
        if tickers and re.search(r"\b(precio|cotizacion|quote)\b", norm):
            return "quote", {"ticker": tickers[0]}
        if tickers and re.search(r"\b(tecnico|playbook|grafico)\b", norm):
            return "technical", {"ticker": tickers[0]}

        return "unknown", {}

    @staticmethod
    def _parse_qty(raw: str) -> float:
        try:
            return float(str(raw).replace(",", "."))
        except ValueError:
            return 1.0

    def _resolve_ticker(self, fragment: str, raw: str) -> str | None:
        frag = fragment.strip(" .,!?:;")
        norm_frag = self._normalize(frag)
        # Multi-word aliases first (e.g. "coca cola")
        for alias, ticker in sorted(_TICKER_ALIASES.items(), key=lambda x: -len(x[0])):
            if norm_frag == alias or norm_frag.startswith(alias + " ") or f" {alias} " in f" {norm_frag} ":
                return ticker
        tickers = extract_tickers(frag.upper()) or extract_tickers(raw.upper())
        if tickers:
            return tickers[0]
        word = frag.split()[0].upper() if frag else ""
        if 1 < len(word) <= 5 and word.isalpha():
            return word
        return None

    async def _help(self, session, params, portfolio_id) -> VoiceCommandResult:
        lines = [f"Di: {h.phrase}. {h.description}." for h in _HELP_ITEMS[:8]]
        return VoiceCommandResult(
            intent="help",
            speech="Puedo hablarte del mercado, dar precios, técnico, comprar o vender con confirmación, "
            "ver posiciones y más. " + " ".join(lines),
            data={"commands": [h.model_dump() for h in _HELP_ITEMS]},
        )

    async def _app_help(self, session, params, portfolio_id) -> VoiceCommandResult:
        return VoiceCommandResult(
            intent="app_help",
            speech=(
                "Monarch Capital es tu comité de inversión: panel de mercado, análisis multiagente, "
                "watchlist, recomendaciones de corto plazo y ejecución Alpaca. "
                "Usa el micrófono o escribe comandos. Di ayuda para ver frases de voz."
            ),
            data={"app": "Monarch Capital"},
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

        sent_label = getattr(overview, "market_sentiment_label", None) or "neutral"
        sent_score = getattr(overview, "market_sentiment_score", None)
        sent_es = {
            "bullish": "alcista",
            "bearish": "bajista",
            "neutral": "neutral",
            "positive": "positivo",
            "negative": "negativo",
        }.get(str(sent_label).lower(), str(sent_label))
        if sent_score is not None:
            parts.append(f"Sentimiento agregado {sent_es}, score {float(sent_score):+.1f}.")
        else:
            parts.append(f"Sentimiento agregado {sent_es}.")

        report = await ReportRepository(session).get_latest_daily_report()
        if report and report.market_report:
            summary = (report.market_report.market_summary or "")[:400]
            if summary:
                parts.append(f"Briefing: {summary}")

        try:
            st = await AlpacaOrderService().status()
            if st.message:
                parts.append(f"Alpaca: {st.message}")
        except Exception as exc:
            logger.debug("voice.alpaca_pulse_failed", error=str(exc))

        return VoiceCommandResult(
            intent="market",
            speech=" ".join(parts),
            data={
                "regime": overview.market_regime,
                "score": overview.market_regime_score,
                "sentiment_label": sent_label,
                "sentiment_score": sent_score,
            },
            ui_action="refresh",
        )

    async def _quote(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        market = get_market_provider()
        try:
            quote = await market.get_quote(ticker)
        except Exception as exc:
            return VoiceCommandResult(
                intent="quote",
                success=False,
                speech=f"No pude obtener el precio de {ticker}: {exc}",
            )

        price = quote.get("current_price")
        name = quote.get("company_name") or ticker
        if price is None:
            return VoiceCommandResult(
                intent="quote",
                success=False,
                speech=f"No encontré cotización actual para {ticker}.",
                params={"ticker": ticker},
            )

        parts = [f"{ticker}, {name}, cotiza a ${float(price):,.2f}."]
        change_5d = None
        try:
            hist = await market.get_history(ticker, period="5d", interval="1d")
            if hist is not None and len(hist) >= 2 and "Close" in hist.columns:
                first = float(hist["Close"].iloc[0])
                last = float(hist["Close"].iloc[-1])
                if first:
                    change_5d = (last - first) / first * 100.0
                    parts.append(f"En 5 días lleva {change_5d:+.1f} por ciento.")
        except Exception as exc:
            logger.debug("voice.quote_5d_failed", ticker=ticker, error=str(exc))

        return VoiceCommandResult(
            intent="quote",
            speech=" ".join(parts),
            params={"ticker": ticker},
            data={"price": float(price), "change_5d_pct": change_5d, "name": name},
        )

    async def _technical(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        chart = await TechnicalChartService(get_market_provider()).build(ticker)
        playbook = chart.playbook or {}
        speech = (
            playbook.get("summary")
            or chart.summary
            or f"No hay playbook técnico disponible para {ticker}."
        )
        return VoiceCommandResult(
            intent="technical",
            speech=str(speech)[:700],
            params={"ticker": ticker},
            data={
                "summary": chart.summary,
                "playbook": playbook,
                "bias": getattr(chart, "bias", None),
            },
            ui_action="scroll:tech-view",
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
        from services.analysis_factory import build_analysis_service

        svc = DailyTradeRecommendationService(
            market_provider=market,
            discovery_service=CompanyDiscoveryService(market_provider=market),
            trade_repo=DailyTradeRepository(session),
            analysis_service=build_analysis_service(session),
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

    async def _broker_status(self, session, params, portfolio_id) -> VoiceCommandResult:
        svc = AlpacaOrderService()
        st = await svc.status()
        mode = "paper" if st.paper else "LIVE"
        speech = st.message or f"Alpaca {mode}: {'conectada' if st.connected else 'sin conexión'}."
        data = st.model_dump() if hasattr(st, "model_dump") else {"message": speech}
        return VoiceCommandResult(
            intent="broker",
            speech=speech,
            data=data,
            success=bool(st.configured),
        )

    async def _positions(self, session, params, portfolio_id) -> VoiceCommandResult:
        svc = AlpacaOrderService()
        if not svc.is_configured():
            return VoiceCommandResult(
                intent="positions",
                success=False,
                speech="Alpaca no está configurada. Añade las API keys para ver posiciones.",
            )
        try:
            positions = await svc.get_positions()
        except Exception as exc:
            return VoiceCommandResult(
                intent="positions",
                success=False,
                speech=f"No pude leer posiciones de Alpaca: {exc}",
            )
        if not positions:
            return VoiceCommandResult(
                intent="positions",
                speech="No tienes posiciones abiertas en Alpaca.",
                data={"positions": []},
            )

        lines = []
        for p in positions[:8]:
            pl = f", P/L {p.unrealized_pl:+.2f}" if p.unrealized_pl is not None else ""
            lines.append(f"{p.symbol} {p.qty:g} @ ${p.current_price or p.avg_entry_price:.2f}{pl}")
        extra = f" Y {len(positions) - 8} más." if len(positions) > 8 else ""
        speech = f"Tienes {len(positions)} posiciones. " + ". ".join(lines) + "." + extra
        return VoiceCommandResult(
            intent="positions",
            speech=speech,
            data={"positions": [p.model_dump() for p in positions]},
        )

    async def _buy_preview(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        shares = float(params.get("shares") or 1.0)
        if shares <= 0:
            return VoiceCommandResult(
                intent="buy",
                success=False,
                speech="Indica una cantidad positiva. Ejemplo: compra 1 AAPL.",
            )

        svc = AlpacaOrderService()
        if not svc.is_configured():
            return VoiceCommandResult(
                intent="buy",
                success=False,
                speech="Alpaca no está configurada. No puedo previsualizar la compra.",
            )

        preview = await svc.execute(
            ExecuteOrdersRequest(
                lines=[ExecuteLine(ticker=ticker, shares=shares, side="buy")],
                dry_run=True,
            )
        )
        if preview.failed and not preview.submitted:
            err = preview.failed[0].error or "orden rechazada en simulación"
            return VoiceCommandResult(
                intent="buy",
                success=False,
                speech=f"No pude preparar la compra de {shares:g} {ticker}: {err}",
                data={"preview": preview.model_dump()},
            )

        mode = "paper" if svc.paper else "LIVE"
        pending = {
            "kind": "buy",
            "ticker": ticker,
            "shares": shares,
            "close_all": False,
            "preview": preview.model_dump(),
        }
        _set_pending(portfolio_id, pending)
        warn = (" " + " ".join(preview.warnings)) if preview.warnings else ""
        speech = (
            f"Previsualización {mode}: comprar {shares:g} acciones de {ticker}. "
            f"Di confirma para ejecutar, o cancela para descartar.{warn}"
        )
        return VoiceCommandResult(
            intent="buy",
            speech=speech.strip(),
            params={"ticker": ticker, "shares": shares},
            data={"preview": preview.model_dump()},
            requires_confirmation=True,
            pending_action={k: v for k, v in pending.items() if not str(k).startswith("_")},
        )

    async def _sell_preview(self, session, params, portfolio_id) -> VoiceCommandResult:
        ticker = params["ticker"].upper()
        close_all = bool(params.get("close_all"))
        shares = float(params.get("shares") or 0.0)
        svc = AlpacaOrderService()
        if not svc.is_configured():
            return VoiceCommandResult(
                intent="sell",
                success=False,
                speech="Alpaca no está configurada. No puedo previsualizar la venta.",
            )

        mode = "paper" if svc.paper else "LIVE"
        held_qty = None
        try:
            positions = await svc.get_positions()
            match = next((p for p in positions if p.symbol.upper() == ticker), None)
            if match:
                held_qty = float(match.qty)
        except Exception as exc:
            logger.debug("voice.sell_positions_lookup", error=str(exc))

        if close_all or shares <= 0:
            if held_qty is not None and held_qty <= 0:
                return VoiceCommandResult(
                    intent="sell",
                    success=False,
                    speech=f"No tienes posición abierta en {ticker} para cerrar.",
                )
            qty_txt = f" ({held_qty:g} acciones)" if held_qty is not None else ""
            pending = {
                "kind": "sell_close",
                "ticker": ticker,
                "shares": held_qty,
                "close_all": True,
            }
            _set_pending(portfolio_id, pending)
            speech = (
                f"Previsualización {mode}: cerrar toda la posición de {ticker}{qty_txt}. "
                "Di confirma para ejecutar, o cancela para descartar."
            )
            return VoiceCommandResult(
                intent="sell",
                speech=speech,
                params={"ticker": ticker, "close_all": True},
                requires_confirmation=True,
                pending_action={k: v for k, v in pending.items() if not str(k).startswith("_")},
            )

        if held_qty is not None and shares > held_qty + 1e-9:
            return VoiceCommandResult(
                intent="sell",
                success=False,
                speech=(
                    f"Solo tienes {held_qty:g} de {ticker}. "
                    f"No puedo vender {shares:g}. Di vende todo {ticker} para cerrar."
                ),
            )

        preview = await svc.execute(
            ExecuteOrdersRequest(
                lines=[ExecuteLine(ticker=ticker, shares=shares, side="sell")],
                dry_run=True,
            )
        )
        if preview.failed and not preview.submitted:
            err = preview.failed[0].error or "orden rechazada en simulación"
            return VoiceCommandResult(
                intent="sell",
                success=False,
                speech=f"No pude preparar la venta de {shares:g} {ticker}: {err}",
                data={"preview": preview.model_dump()},
            )

        pending = {
            "kind": "sell",
            "ticker": ticker,
            "shares": shares,
            "close_all": False,
            "preview": preview.model_dump(),
        }
        _set_pending(portfolio_id, pending)
        speech = (
            f"Previsualización {mode}: vender {shares:g} acciones de {ticker}. "
            "Di confirma para ejecutar, o cancela para descartar."
        )
        return VoiceCommandResult(
            intent="sell",
            speech=speech,
            params={"ticker": ticker, "shares": shares},
            data={"preview": preview.model_dump()},
            requires_confirmation=True,
            pending_action={k: v for k, v in pending.items() if not str(k).startswith("_")},
        )

    async def _confirm(self, session, params, portfolio_id) -> VoiceCommandResult:
        pending = _get_pending(portfolio_id)
        if not pending:
            return VoiceCommandResult(
                intent="confirm",
                success=False,
                speech="No hay ninguna orden pendiente. Di por ejemplo: compra 1 AAPL, y luego confirma.",
            )

        svc = AlpacaOrderService()
        if not svc.is_configured():
            _clear_pending(portfolio_id)
            return VoiceCommandResult(
                intent="confirm",
                success=False,
                speech="Alpaca no está configurada. Cancelé la orden pendiente.",
            )

        kind = pending.get("kind")
        ticker = str(pending.get("ticker") or "").upper()
        shares = pending.get("shares")
        confirm_live = not svc.paper

        try:
            if kind == "sell_close" or (kind == "sell" and pending.get("close_all")):
                raw = await svc.close_position(ticker)
                _clear_pending(portfolio_id)
                return VoiceCommandResult(
                    intent="confirm",
                    speech=f"Listo. Cerré la posición de {ticker} en Alpaca.",
                    params={"ticker": ticker, "kind": "sell_close"},
                    data={"result": raw},
                    ui_action="refresh",
                )

            if kind not in ("buy", "sell"):
                _clear_pending(portfolio_id)
                return VoiceCommandResult(
                    intent="confirm",
                    success=False,
                    speech="La acción pendiente no es válida. Vuelve a pedir compra o venta.",
                )

            qty = float(shares or 0)
            if qty <= 0:
                _clear_pending(portfolio_id)
                return VoiceCommandResult(
                    intent="confirm",
                    success=False,
                    speech="La cantidad pendiente no es válida. Reformula el comando.",
                )

            side = "buy" if kind == "buy" else "sell"
            result = await svc.execute(
                ExecuteOrdersRequest(
                    lines=[ExecuteLine(ticker=ticker, shares=qty, side=side)],
                    dry_run=False,
                    confirm_live=confirm_live,
                    sync_portfolio_id=portfolio_id,
                )
            )
            _clear_pending(portfolio_id)

            if result.warnings and not result.submitted:
                return VoiceCommandResult(
                    intent="confirm",
                    success=False,
                    speech="No se envió la orden: " + " ".join(result.warnings),
                    data={"result": result.model_dump()},
                )

            if result.failed and not result.submitted:
                err = result.failed[0].error or "falló en Alpaca"
                return VoiceCommandResult(
                    intent="confirm",
                    success=False,
                    speech=f"La orden de {side} {qty:g} {ticker} falló: {err}",
                    data={"result": result.model_dump()},
                )

            verb = "compré" if side == "buy" else "vendí"
            mode = "paper" if result.paper else "LIVE"
            speech = f"Hecho ({mode}): {verb} {qty:g} de {ticker}."
            if result.warnings:
                speech += " " + " ".join(result.warnings)
            return VoiceCommandResult(
                intent="confirm",
                speech=speech,
                params={"ticker": ticker, "shares": qty, "side": side},
                data={"result": result.model_dump()},
                ui_action="refresh",
            )
        except Exception as exc:
            _clear_pending(portfolio_id)
            return VoiceCommandResult(
                intent="confirm",
                success=False,
                speech=f"Error al confirmar la orden: {exc}",
            )

    async def _cancel_pending(self, session, params, portfolio_id) -> VoiceCommandResult:
        pending = _get_pending(portfolio_id)
        _clear_pending(portfolio_id)
        if not pending:
            return VoiceCommandResult(
                intent="cancel_pending",
                speech="No había ninguna orden pendiente que cancelar.",
            )
        ticker = pending.get("ticker") or ""
        return VoiceCommandResult(
            intent="cancel_pending",
            speech=f"Cancelé la orden pendiente de {ticker}.".strip()
            if ticker
            else "Cancelé la orden pendiente.",
            data={"cancelled": {k: v for k, v in pending.items() if not str(k).startswith("_")}},
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
