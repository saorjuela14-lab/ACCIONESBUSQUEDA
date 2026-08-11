"""Research pack assembly."""

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from services.research_pack_service import ResearchPackService


@pytest.mark.asyncio
async def test_research_pack_builds_why_and_htf():
    market = AsyncMock()
    market.get_quote = AsyncMock(
        return_value={"current_price": 42.0, "change_pct": 3.5}
    )
    idx = pd.date_range("2024-01-01", periods=40, freq="D")
    close = pd.Series(range(40), index=idx, dtype=float) + 20
    market.get_history = AsyncMock(
        return_value=pd.DataFrame(
            {
                "Open": close - 0.5,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": [1_000_000] * 39 + [3_000_000],
            },
            index=idx,
        )
    )
    news = AsyncMock()
    news.get_company_news = AsyncMock(
        return_value=[{"title": "Earnings beat", "source": "TestWire", "published_at": "2024-06-01"}]
    )

    pack = await ResearchPackService(market=market, news=news).build("VRT", enrich_llm=False)
    assert pack.ticker == "VRT"
    assert pack.why_moving
    assert pack.htf_trend.get("ticker") == "VRT"
    assert any("Volumen" in w or "Variación" in w or "precio" in w.lower() for w in pack.why_moving)
    assert pack.upcoming_news and pack.upcoming_news[0]["title"] == "Earnings beat"
