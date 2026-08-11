"""Unified research pack per ticker — why / flow proxies / news / HTF / levels."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from providers.interfaces import MarketDataProvider, NewsProvider
from services.htf_trend_filter import HtfTrendFilter
from utils.logging import get_logger

logger = get_logger(__name__)


class ResearchPack(BaseModel):
    ticker: str
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    why_moving: list[str] = Field(default_factory=list)
    capital_flow_proxies: dict[str, Any] = Field(default_factory=dict)
    upcoming_news: list[dict[str, Any]] = Field(default_factory=list)
    htf_trend: dict[str, Any] = Field(default_factory=dict)
    quote: dict[str, Any] = Field(default_factory=dict)
    narrative: str | None = None
    disclaimer: str = (
        "Proxies de flujo (volumen relativo / sentimiento), no dark-pool ni 13F en tiempo real. "
        "Información de mesa; no es consejo de inversión personalizado."
    )


class ResearchPackService:
    def __init__(
        self,
        market: MarketDataProvider,
        news: NewsProvider | None = None,
        sentiment_provider: Any | None = None,
    ) -> None:
        self._market = market
        self._news = news
        self._sentiment = sentiment_provider
        self._htf = HtfTrendFilter(market)

    async def build(self, ticker: str, *, enrich_llm: bool = True) -> ResearchPack:
        sym = ticker.upper().strip()
        why: list[str] = []
        flow: dict[str, Any] = {}
        headlines: list[dict[str, Any]] = []
        quote: dict[str, Any] = {}

        try:
            quote = await self._market.get_quote(sym) or {}
            chg = quote.get("change_pct") or quote.get("changePercent")
            if chg is not None:
                why.append(f"Variación reciente: {float(chg):+.2f}%")
            px = quote.get("current_price") or quote.get("price")
            if px:
                why.append(f"Último precio ≈ ${float(px):.2f}")
        except Exception as exc:
            logger.warning("research_pack.quote_failed", ticker=sym, error=str(exc))

        # Volume / RVOL proxy from daily history + Finviz delayed snapshot fields
        try:
            df = await self._market.get_history(sym, period="3mo", interval="1d")
            if df is not None and not getattr(df, "empty", True) and "Volume" in df.columns:
                vol = df["Volume"].dropna()
                if len(vol) >= 5:
                    last = float(vol.iloc[-1])
                    avg20 = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean())
                    rvol = (last / avg20) if avg20 > 0 else None
                    flow["avg_volume_20d"] = round(avg20, 0)
                    flow["last_volume"] = round(last, 0)
                    if rvol is not None:
                        flow["relative_volume"] = round(rvol, 2)
                        if rvol >= 1.5:
                            why.append(f"Volumen elevado ({rvol:.1f}× media 20d) — posible interés institucional/retail")
                        elif rvol <= 0.6:
                            why.append(f"Volumen bajo ({rvol:.1f}× media 20d)")
        except Exception as exc:
            logger.warning("research_pack.volume_failed", ticker=sym, error=str(exc))

        try:
            q = quote or await self._market.get_quote(sym)
            fv_bits = {
                "rel_volume": q.get("rel_volume"),
                "short_float_pct": q.get("short_float_pct"),
                "insider_own_pct": q.get("insider_own_pct"),
                "inst_own_pct": q.get("inst_own_pct"),
                "perf_week_pct": q.get("perf_week_pct"),
                "perf_month_pct": q.get("perf_month_pct"),
                "target_price": q.get("target_price"),
                "analyst_recom": q.get("analyst_recom"),
                "pe": q.get("pe"),
            }
            fv_bits = {k: v for k, v in fv_bits.items() if v is not None}
            if fv_bits:
                flow["finviz"] = fv_bits
                if fv_bits.get("rel_volume") is not None and "relative_volume" not in flow:
                    flow["relative_volume"] = fv_bits["rel_volume"]
                sf = fv_bits.get("short_float_pct")
                if sf is not None and float(sf) >= 10:
                    why.append(f"Short float Finviz ≈ {float(sf):.1f}%")
                pw = fv_bits.get("perf_week_pct")
                if pw is not None:
                    why.append(f"Perf. semanal Finviz {float(pw):+.1f}% (dato diferido)")
        except Exception as exc:
            logger.warning("research_pack.finviz_failed", ticker=sym, error=str(exc))

        # Sentiment proxy (institutional channel is keyword-based — label honestly)
        if self._sentiment is not None:
            try:
                if hasattr(self._sentiment, "get_sentiment"):
                    sent = await self._sentiment.get_sentiment(sym)
                elif hasattr(self._sentiment, "analyze"):
                    sent = await self._sentiment.analyze(sym)
                else:
                    sent = None
                if isinstance(sent, dict):
                    flow["sentiment"] = {
                        "label": sent.get("label") or sent.get("aggregated_label"),
                        "score": sent.get("aggregated_score") or sent.get("score"),
                        "institutional_proxy": sent.get("institutional_score"),
                        "retail_proxy": sent.get("retail_score"),
                    }
                    inst = sent.get("institutional_score")
                    if inst is not None and float(inst) > 0.2:
                        why.append("Proxy institucional en noticias/redes (keywords) en terreno positivo")
            except Exception as exc:
                logger.warning("research_pack.sentiment_failed", ticker=sym, error=str(exc))

        if self._news is not None:
            try:
                items = []
                if hasattr(self._news, "get_company_news"):
                    items = await self._news.get_company_news(sym, max_results=8)
                elif hasattr(self._news, "search_news"):
                    items = await self._news.search_news(sym, max_results=8)
                for item in items or []:
                    if hasattr(item, "title"):
                        headlines.append(
                            {
                                "title": getattr(item, "title", "") or "",
                                "source": getattr(item, "source", "") or "",
                                "published": str(getattr(item, "published_at", "") or ""),
                                "url": getattr(item, "url", "") or "",
                            }
                        )
                    elif isinstance(item, dict):
                        headlines.append(
                            {
                                "title": item.get("title") or item.get("headline") or "",
                                "source": item.get("source") or item.get("publisher") or "",
                                "published": str(item.get("published_at") or item.get("date") or ""),
                                "url": item.get("url") or item.get("link") or "",
                            }
                        )
                if headlines:
                    why.append(f"{len(headlines)} titulares recientes revisados")
            except Exception as exc:
                logger.warning("research_pack.news_failed", ticker=sym, error=str(exc))

        htf = await self._htf.evaluate(sym)
        if htf.passed:
            why.append("Tendencia alcista en semanal y mensual (estructura HH/HL)")
        else:
            why.append(htf.reason)

        if not why:
            why.append("Sin catalizadores claros en datos disponibles — revisar manualmente")

        narrative = " · ".join(why[:4]) if why else None
        _ = enrich_llm  # reserved for optional LLM polish later

        return ResearchPack(
            ticker=sym,
            why_moving=why,
            capital_flow_proxies=flow,
            upcoming_news=headlines[:8],
            htf_trend=htf.as_dict(),
            quote={
                "price": quote.get("current_price") or quote.get("price"),
                "change_pct": quote.get("change_pct") or quote.get("changePercent"),
            },
            narrative=narrative,
        )
