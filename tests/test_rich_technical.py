"""Tests for richer technical analysis modules."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from agents.technical.confluence import score_confluence
from agents.technical.historical_setups import evaluate_historical_setups
from agents.technical.indicators import enrich_indicators
from agents.technical.market_opinion import (
    build_market_opinion_from_engine,
    build_market_opinion_from_prior,
    tech_vs_market_alignment,
)
from agents.technical.playbook import build_playbook
from agents.technical.structure import classify_structure
from agents.technical.volume_analysis import analyze_volume
from agents.technical.context import PriorContext
from agents.technical_agent import TechnicalAgent
from services.technical_chart_service import TechnicalChartService


def _ohlcv(rows: int = 120, *, trend: float = 0.15, end: str | None = None) -> pd.DataFrame:
    end_ts = pd.Timestamp(end or datetime.now(timezone.utc).date().isoformat())
    idx = pd.date_range(end=end_ts, periods=rows, freq="D")
    closes = [100 + i * trend + np.sin(i / 7) * 2 for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": [c - 0.4 for c in closes],
            "High": [c + 1.2 for c in closes],
            "Low": [c - 1.2 for c in closes],
            "Close": closes,
            "Volume": [1_000_000 + (i % 10) * 80_000 for i in range(rows)],
        },
        index=idx,
    )


def test_structure_detects_uptrend_on_rising_series():
    df = enrich_indicators(_ohlcv(100, trend=0.4))
    out = classify_structure(df)
    assert out["structure"] in ("uptrend", "range")
    assert "label_es" in out


def test_confluence_htf_alignment():
    tfs = {
        "1D": {"bias": "bullish", "score": 3},
        "1W": {"bias": "bullish", "score": 2},
        "4H": {"bias": "bullish", "score": 1},
        "1H": {"bias": "bullish", "score": 1},
        "15m": {"bias": "neutral", "score": 0},
    }
    c = score_confluence(tfs)
    assert c["htf_bias"] == "bullish"
    assert c["aligned_with_htf"] is True
    assert c["score"] > 0
    assert "alcista" in c["label_es"]


def test_volume_and_historical_setups():
    df = enrich_indicators(_ohlcv(150, trend=0.2))
    vol = analyze_volume(df)
    assert vol["volume_ratio"] is not None
    hist = evaluate_historical_setups(df)
    assert "setups" in hist
    assert hist["note"]


def test_playbook_returns_spanish_strategy():
    pb = build_playbook(
        ticker="AAPL",
        price=190.0,
        daily={"rsi": 45, "adx": 28, "bias": "bullish"},
        structure={"structure": "uptrend", "label_es": "alcista (HH/HL)", "confidence": 0.75, "last_low": 180},
        confluence={
            "htf_bias": "bullish",
            "ltf_bias": "bullish",
            "aligned_with_htf": True,
            "label_es": "confluencia alcista",
            "agreement_pct": 80,
            "label": "bullish",
        },
        volume={"volume_confirm": "expansion", "volume_confirm_es": "expansión", "volume_ratio": 1.4, "above_vwap": True},
        historical={"available": True, "best": {"label_es": "Cruce MACD", "hit_rate": 58, "samples": 20, "horizon_bars": 5, "avg_forward_return_pct": 1.2}},
        trade_levels={"stop_loss": 185, "risk_reward_ratio": 2.1},
        unfilled_gaps=2,
    )
    assert pb["strategy"] == "swing_pullback"
    assert "Playbook" in pb["summary"] or "Swing" in pb["strategy_es"]
    assert pb["checklist"]


def test_market_opinion_from_engine_and_alignment():
    report = {
        "aggregated_score": 22.5,
        "aggregated_label": "bullish",
        "confidence": 0.7,
        "summary": "Narrativa positiva en noticias y redes.",
        "news": {"score": 30, "confidence": 0.6, "trend": "rising", "sample_size": 12, "top_factors": ["Earnings beat"]},
        "social": {"score": 18, "confidence": 0.5, "trend": "stable", "sample_size": 40, "top_factors": ["Bullish StockTwits"]},
        "retail": {"score": 10, "confidence": 0.4, "trend": "stable", "sample_size": 20, "top_factors": []},
        "analyst": {"score": 5, "confidence": 0.3, "trend": "stable", "sample_size": 3, "top_factors": []},
        "institutional": {"score": 8, "confidence": 0.3, "trend": "stable", "sample_size": 2, "top_factors": []},
        "sources_used": ["stocktwits", "yfinance_news"],
        "sources_failed": [],
    }
    op = build_market_opinion_from_engine(report)
    assert op["available"] is True
    assert op["label"] == "bullish"
    assert "Earnings beat" in op["top_factors"]
    assert tech_vs_market_alignment("bullish", "bullish")["status"] == "aligned"
    assert tech_vs_market_alignment("bullish", "bearish")["status"] == "diverged"


def test_playbook_diverges_when_market_bearish():
    mkt = build_market_opinion_from_engine({
        "aggregated_score": -25,
        "aggregated_label": "bearish",
        "confidence": 0.65,
        "summary": "Flujo negativo en social/noticias.",
        "news": {"score": -30, "top_factors": ["Downgrade"]},
        "social": {"score": -20, "top_factors": []},
        "retail": {"score": -15, "top_factors": []},
        "analyst": {"score": -10, "top_factors": []},
        "institutional": {"score": -5, "top_factors": []},
    })
    pb = build_playbook(
        ticker="TSLA",
        price=200.0,
        daily={"rsi": 40, "adx": 30, "bias": "bullish"},
        structure={"structure": "uptrend", "label_es": "alcista", "confidence": 0.7, "last_low": 190},
        confluence={
            "htf_bias": "bullish",
            "ltf_bias": "bullish",
            "aligned_with_htf": True,
            "label_es": "confluencia alcista",
            "agreement_pct": 75,
            "label": "bullish",
        },
        volume={"volume_confirm_es": "normal", "volume_ratio": 1.0},
        historical={"available": False},
        trade_levels={"stop_loss": 185, "risk_reward_ratio": 2.0},
        market_opinion=mkt,
    )
    assert pb["strategy"] == "wait"
    assert pb["market_opinion"]["label"] == "bearish"
    assert pb["tech_market_alignment"]["status"] == "diverged"
    assert any("Opinión mercado" in c for c in pb["checklist"])


def test_market_opinion_from_prior_context():
    ctx = PriorContext(
        scores={"sentiment_agent": 20.0, "news_agent": 15.0},
        summaries={"sentiment_agent": "Sentimiento agregado alcista"},
        news_sentiment_score=12.0,
    )
    op = build_market_opinion_from_prior(ctx)
    assert op["available"] is True
    assert op["label"] in ("bullish", "neutral", "bearish")


@pytest.mark.asyncio
async def test_technical_agent_includes_playbook():
    market = MagicMock()
    market.get_quote = AsyncMock(return_value={"current_price": 150.0})
    market.get_history = AsyncMock(return_value=_ohlcv(160, trend=0.25))

    agent = TechnicalAgent(market)
    report = await agent.analyze("AAPL")

    assert report.agent_name == "technical_agent"
    assert report.raw_data.get("playbook")
    assert report.raw_data.get("confluence")
    assert "Playbook" in report.summary or "confluencia" in report.summary.lower() or "estructura" in report.summary.lower()


@pytest.mark.asyncio
async def test_chart_service_exposes_playbook_fields():
    market = MagicMock()
    market.get_history = AsyncMock(return_value=_ohlcv(160, trend=0.2))
    svc = TechnicalChartService(market)
    svc._fetch_market_opinion = AsyncMock(return_value={
        "available": True,
        "label": "bullish",
        "label_es": "alcista",
        "aggregated_score": 15.0,
        "confidence": 0.6,
        "summary": "Mercado constructivo",
        "channels": {"news": {"score": 20}, "social": {"score": 10}},
        "top_factors": ["Positive coverage"],
        "headline": "Opinión de mercado alcista (+15.0)",
    })
    data = await svc.build("AAPL", period="6mo", chart_timeframe="1D")
    assert data.playbook
    assert data.structure
    assert data.confluence
    assert data.snapshot is not None
    assert data.snapshot.adx is not None or data.snapshot.sma200 is not None
    assert data.market_opinion.get("available") is True
    assert data.playbook.get("market_opinion")
