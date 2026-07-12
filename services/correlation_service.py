"""Correlation and market dependency analysis."""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from domain.correlations import (
    CompanyDependency,
    CorrelationPair,
    MacroSensitivity,
    MarketDependencyReport,
)
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)

BENCHMARKS: dict[str, str] = {
    "SPY": "Mercado amplio EE.UU. (S&P 500)",
    "QQQ": "Tecnología / crecimiento EE.UU.",
    "USO": "Precio del crudo",
    "GLD": "Oro / refugio seguro",
    "UUP": "Fortaleza del dólar",
    "TLT": "Tipos de interés largos EE.UU.",
    "EEM": "Mercados emergentes",
    "SOXX": "Sector semiconductores",
    "XLE": "Sector energía",
    "XLF": "Sector financiero",
}

# Sector -> default benchmark ETF
SECTOR_BENCHMARKS: dict[str, str] = {
    "Technology": "QQQ",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}

# Known cross-company / supply-chain dependencies
KNOWN_DEPENDENCIES: dict[str, list[tuple[str, str, str]]] = {
    "TSM": [("NVDA", "cliente", "NVIDIA depende de la fabricación de TSMC para GPUs"),
            ("AMD", "cliente", "Chips AMD fabricados en TSMC"),
            ("AAPL", "cliente", "Chips serie A/M de Apple desde TSMC")],
    "NVDA": [("TSM", "proveedor", "Producción GPU depende de nodos avanzados TSMC"),
             ("AMD", "competidor", "Competencia en GPUs AI/datacenter")],
    "AMD": [("TSM", "proveedor", "Socio de fabricación de chips"),
            ("NVDA", "competidor", "Competencia en aceleradores AI")],
    "AAPL": [("TSM", "proveedor", "Principal socio de fabricación de chips"),
             ("QCOM", "proveedor", "Componentes de módem")],
    "ABBV": [("LLY", "competidor", "Competencia en inmunología / obesidad adyacente"),
             ("PFE", "competidor", "Grupo big pharma pares")],
}

# Macro factor rules by sector/industry keywords
MACRO_RULES: list[dict] = [
    {
        "match_sectors": ["Energy"],
        "factor": "Precio del petróleo",
        "proxy": "USO",
        "sensitivity": "high",
        "scenario": "Cierre del Estrecho de Ormuz o recorte OPEP",
        "impact": "Ingresos y márgenes energéticos suelen subir con el crudo; acción muy sensible al petróleo.",
    },
    {
        "match_sectors": ["Industrials", "Consumer Cyclical", "Airlines"],
        "factor": "Precio del petróleo",
        "proxy": "USO",
        "sensitivity": "medium",
        "scenario": "Subida del petróleo por disrupción en Medio Oriente",
        "impact": "Costes de insumos y márgenes de transporte presionados; aerolíneas especialmente vulnerables.",
    },
    {
        "match_sectors": ["Technology"],
        "factor": "Ciclo de semiconductores",
        "proxy": "SOXX",
        "sensitivity": "high",
        "scenario": "Capacidad TSMC / riesgo geopolítico en Taiwán",
        "impact": "Oferta y precios de chips afectan toda la cadena tech (NVDA, AMD, AAPL).",
    },
    {
        "match_keywords": ["semiconductor", "chip", "foundry"],
        "factor": "Ciclo de semiconductores",
        "proxy": "SOXX",
        "sensitivity": "high",
        "scenario": "Disrupción de producción TSM",
        "impact": "Impacto directo en ingresos y plazos de entrega de clientes fabless.",
    },
    {
        "match_sectors": ["Financial Services"],
        "factor": "Tipos de interés",
        "proxy": "TLT",
        "sensitivity": "high",
        "scenario": "Subidas o bajadas de tipos de la Fed",
        "impact": "Márgenes de interés neto y demanda de crédito cambian con el ciclo de tipos.",
    },
    {
        "match_sectors": ["Healthcare", "Drug Manufacturers"],
        "factor": "Fortaleza del USD",
        "proxy": "UUP",
        "sensitivity": "medium",
        "scenario": "Dólar fuerte perjudica conversión de ingresos internacionales",
        "impact": "Ingresos de pharma multinacional pueden comprimirse cuando el USD sube.",
    },
    {
        "match_all": True,
        "factor": "Mercados emergentes",
        "proxy": "EEM",
        "sensitivity": "medium",
        "scenario": "Desaceleración EM o fuga de capitales",
        "impact": "Multinacionales con ventas EM enfrentan vientos en contra de demanda.",
    },
]


class CorrelationService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def _returns(self, ticker: str, period: str = "1y") -> pd.Series:
        df = await self._market.get_history(ticker, period=period, interval="1d")
        if df.empty or "Close" not in df.columns:
            return pd.Series(dtype=float)
        return df["Close"].pct_change().dropna()

    async def _corr(self, a: str, b: str) -> float | None:
        ra, rb = await asyncio.gather(self._returns(a), self._returns(b))
        if ra.empty or rb.empty:
            return None
        joined = pd.concat([ra, rb], axis=1, join="inner").dropna()
        if len(joined) < 20:
            return None
        return float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))

    def _interpret_corr(self, benchmark: str, corr: float, label: str) -> str:
        strength = "fuerte" if abs(corr) >= 0.6 else "moderada" if abs(corr) >= 0.35 else "débil"
        direction = "positiva" if corr >= 0 else "inversa"
        return f"vinculación {strength} {direction} con {benchmark} ({label})"

    def _macro_rules_for(self, sector: str | None, industry: str | None) -> list[dict]:
        sector = sector or ""
        industry = (industry or "").lower()
        matched: list[dict] = []
        seen: set[str] = set()

        for rule in MACRO_RULES:
            if rule.get("match_all"):
                key = rule["factor"]
                if key not in seen:
                    matched.append(rule)
                    seen.add(key)
                continue
            sectors = rule.get("match_sectors", [])
            keywords = rule.get("match_keywords", [])
            if sector in sectors or any(k in industry for k in keywords):
                key = rule["factor"]
                if key not in seen:
                    matched.append(rule)
                    seen.add(key)
        return matched

    async def analyze(self, ticker: str) -> MarketDependencyReport:
        ticker = ticker.upper()
        quote = await self._market.get_quote(ticker)
        sector = quote.get("sector")
        industry = quote.get("industry")
        company_name = quote.get("company_name", ticker)

        # Benchmark correlations
        bench_tickers = list(BENCHMARKS.keys())
        if sector in SECTOR_BENCHMARKS:
            etf = SECTOR_BENCHMARKS[sector]
            if etf not in bench_tickers:
                bench_tickers.append(etf)

        corr_pairs: list[CorrelationPair] = []
        corr_tasks = {b: self._corr(ticker, b) for b in bench_tickers}
        results = await asyncio.gather(*corr_tasks.values())
        for bench, corr in zip(corr_tasks.keys(), results):
            if corr is None:
                continue
            label = BENCHMARKS.get(bench, f"proxy sectorial {sector}" if bench in SECTOR_BENCHMARKS.values() else bench)
            corr_pairs.append(
                CorrelationPair(
                    ticker=bench,
                    correlation=round(corr, 3),
                    relationship=label,
                    interpretation=self._interpret_corr(bench, corr, label),
                )
            )
        corr_pairs.sort(key=lambda p: abs(p.correlation), reverse=True)

        # Macro sensitivities with live correlation to proxy
        macro_list: list[MacroSensitivity] = []
        for rule in self._macro_rules_for(sector, industry):
            proxy = rule["proxy"]
            live_corr = await self._corr(ticker, proxy)
            macro_list.append(
                MacroSensitivity(
                    factor=rule["factor"],
                    proxy_ticker=proxy,
                    correlation=round(live_corr, 3) if live_corr is not None else None,
                    sensitivity=rule["sensitivity"],
                    scenario=rule["scenario"],
                    impact_if_shock=rule["impact"],
                )
            )

        # Company dependencies: known + peers
        deps: list[CompanyDependency] = []
        for dep_ticker, rel, why in KNOWN_DEPENDENCIES.get(ticker, []):
            peer_corr = await self._corr(ticker, dep_ticker)
            deps.append(
                CompanyDependency(
                    ticker=dep_ticker,
                    relationship=rel,
                    correlation=round(peer_corr, 3) if peer_corr is not None else None,
                    why_it_matters=why,
                )
            )

        try:
            peers = await self._market.get_peers(ticker)
            for peer in peers[:5]:
                if peer == ticker or any(d.ticker == peer for d in deps):
                    continue
                peer_corr = await self._corr(ticker, peer)
                deps.append(
                    CompanyDependency(
                        ticker=peer,
                        relationship="sector_peer",
                        correlation=round(peer_corr, 3) if peer_corr is not None else None,
                        why_it_matters=f"Par del sector — suele moverse en correlación con {ticker}",
                    )
                )
        except Exception as exc:
            logger.warning("correlation.peers.failed", ticker=ticker, error=str(exc))

        eem_corr = next((p.correlation for p in corr_pairs if p.ticker == "EEM"), None)
        if eem_corr is not None and abs(eem_corr) >= 0.4:
            em_exposure = f"Sensibilidad moderada-alta a mercados emergentes (corr EEM {eem_corr:+.2f})"
        elif eem_corr is not None:
            em_exposure = f"Sensibilidad limitada a mercados emergentes (corr EEM {eem_corr:+.2f})"
        else:
            em_exposure = "Exposición a mercados emergentes no cuantificable con datos disponibles"

        top = corr_pairs[:3]
        top_text = "; ".join(f"{p.ticker} ({p.correlation:+.2f})" for p in top) if top else "datos insuficientes"
        macro_text = "; ".join(f"{m.factor} ({m.sensitivity})" for m in macro_list[:3])
        summary = (
            f"{ticker} ({sector or 'sector desconocido'}) — vínculos benchmark más fuertes: {top_text}. "
            f"Sensibilidades macro clave: {macro_text or 'ninguna mapeada'}. "
            f"{em_exposure}."
        )

        risk_score = 0.0
        for p in corr_pairs:
            if p.ticker == "USO" and p.correlation > 0.5:
                risk_score += 15
            if p.ticker == "TLT" and abs(p.correlation) > 0.5:
                risk_score += 10
        risk_score = min(100.0, risk_score)

        return MarketDependencyReport(
            ticker=ticker,
            sector=sector,
            industry=industry,
            benchmark_correlations=corr_pairs,
            macro_sensitivities=macro_list,
            company_dependencies=deps[:10],
            emerging_market_exposure=em_exposure,
            summary=summary,
            risk_score=risk_score,
        )
