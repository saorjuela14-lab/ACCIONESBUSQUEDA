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
    "SPY": "US broad market (S&P 500)",
    "QQQ": "US technology / growth",
    "USO": "Crude oil prices",
    "GLD": "Gold / safe haven",
    "UUP": "US dollar strength",
    "TLT": "Long-term US interest rates",
    "EEM": "Emerging markets",
    "SOXX": "Semiconductors sector",
    "XLE": "Energy sector",
    "XLF": "Financials sector",
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
    "TSM": [("NVDA", "customer", "NVIDIA depends on TSMC fabrication for GPUs"),
            ("AMD", "customer", "AMD chips manufactured at TSMC"),
            ("AAPL", "customer", "Apple A-series/M-series chips from TSMC")],
    "NVDA": [("TSM", "supplier", "GPU production relies on TSMC advanced nodes"),
             ("AMD", "competitor", "Competing AI/datacenter GPU market")],
    "AMD": [("TSM", "supplier", "Chip fabrication partner"),
            ("NVDA", "competitor", "AI accelerator competition")],
    "AAPL": [("TSM", "supplier", "Primary chip manufacturing partner"),
             ("QCOM", "supplier", "Modem components")],
    "ABBV": [("LLY", "competitor", "Competing immunology / obesity adjacent"),
             ("PFE", "competitor", "Big pharma peer group")],
}

# Macro factor rules by sector/industry keywords
MACRO_RULES: list[dict] = [
    {
        "match_sectors": ["Energy"],
        "factor": "Oil price",
        "proxy": "USO",
        "sensitivity": "high",
        "scenario": "Strait of Hormuz closure or OPEC supply cut",
        "impact": "Energy revenues and margins typically rise with crude; stock highly oil-sensitive.",
    },
    {
        "match_sectors": ["Industrials", "Consumer Cyclical", "Airlines"],
        "factor": "Oil price",
        "proxy": "USO",
        "sensitivity": "medium",
        "scenario": "Oil spike from Middle East disruption",
        "impact": "Input costs and transport margins pressured; airlines especially vulnerable.",
    },
    {
        "match_sectors": ["Technology"],
        "factor": "Semiconductor cycle",
        "proxy": "SOXX",
        "sensitivity": "high",
        "scenario": "TSMC capacity / Taiwan geopolitical risk",
        "impact": "Chip supply and pricing affect entire tech chain (NVDA, AMD, AAPL).",
    },
    {
        "match_keywords": ["semiconductor", "chip", "foundry"],
        "factor": "Semiconductor cycle",
        "proxy": "SOXX",
        "sensitivity": "high",
        "scenario": "TSM production disruption",
        "impact": "Direct revenue and lead-time impact across fabless customers.",
    },
    {
        "match_sectors": ["Financial Services"],
        "factor": "Interest rates",
        "proxy": "TLT",
        "sensitivity": "high",
        "scenario": "Fed rate hikes or cuts",
        "impact": "Net interest margins and loan demand shift with rate cycle.",
    },
    {
        "match_sectors": ["Healthcare", "Drug Manufacturers"],
        "factor": "USD strength",
        "proxy": "UUP",
        "sensitivity": "medium",
        "scenario": "Strong dollar hurts international revenue translation",
        "impact": "Multinational pharma revenues can compress when USD rallies.",
    },
    {
        "match_all": True,
        "factor": "Emerging markets",
        "proxy": "EEM",
        "sensitivity": "medium",
        "scenario": "EM slowdown or capital flight",
        "impact": "Global multinationals with EM sales exposure face demand headwinds.",
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
        strength = "strong" if abs(corr) >= 0.6 else "moderate" if abs(corr) >= 0.35 else "weak"
        direction = "positive" if corr >= 0 else "inverse"
        return f"{strength} {direction} linkage with {benchmark} ({label})"

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
            label = BENCHMARKS.get(bench, f"{sector} sector proxy" if bench in SECTOR_BENCHMARKS.values() else bench)
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
                        why_it_matters=f"Sector peer — moves often correlate with {ticker}",
                    )
                )
        except Exception as exc:
            logger.warning("correlation.peers.failed", ticker=ticker, error=str(exc))

        eem_corr = next((p.correlation for p in corr_pairs if p.ticker == "EEM"), None)
        if eem_corr is not None and abs(eem_corr) >= 0.4:
            em_exposure = f"Moderate-to-high EM sensitivity (EEM corr {eem_corr:+.2f})"
        elif eem_corr is not None:
            em_exposure = f"Limited EM sensitivity (EEM corr {eem_corr:+.2f})"
        else:
            em_exposure = "EM exposure could not be quantified from available data"

        top = corr_pairs[:3]
        top_text = "; ".join(f"{p.ticker} ({p.correlation:+.2f})" for p in top) if top else "insufficient data"
        macro_text = "; ".join(f"{m.factor} ({m.sensitivity})" for m in macro_list[:3])
        summary = (
            f"{ticker} ({sector or 'Unknown sector'}) — strongest benchmark links: {top_text}. "
            f"Key macro sensitivities: {macro_text or 'none mapped'}. "
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
