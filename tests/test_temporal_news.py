"""Temporal news analysis tests."""

from datetime import datetime, timezone

from domain.enums import NewsSentiment, NewsTopicCategory
from domain.reports import NewsItem
from providers.news.intelligence import enrich_news_item
from providers.news.temporal_analysis import (
    NewsTimeline,
    build_temporal_report,
    detect_ongoing_issues,
)


def _item(title: str, snippet: str, days_ago: int = 0, sentiment=NewsSentiment.NEUTRAL) -> NewsItem:
    from datetime import timedelta

    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return enrich_news_item(
        NewsItem(
            title=title,
            source="test",
            snippet=snippet,
            published_at=published,
            sentiment=sentiment,
        )
    )


def test_detect_ongoing_litigation():
    historical = [
        _item("AbbVie Humira patent lawsuit filed", "Patent dispute with biosimilar makers", days_ago=400),
    ]
    recent = [
        _item("AbbVie patent litigation continues", "Humira biosimilar case ongoing", days_ago=10),
    ]
    themes, narrative = detect_ongoing_issues(recent, historical)
    assert themes
    assert "sigue activo" in narrative.lower() or "heredados" in narrative.lower() or "temas" in narrative.lower()


def test_temporal_report_includes_investment_impact():
    timeline = NewsTimeline(
        recent_3m=[
            _item(
                "FDA approves Skyrizi",
                "Regulatory approval expands market",
                days_ago=5,
                sentiment=NewsSentiment.BULLISH,
            ),
            _item(
                "AbbVie to acquire Apogee",
                "M&A deal worth billions",
                days_ago=3,
                sentiment=NewsSentiment.BULLISH,
            ),
        ],
        historical_2y=[
            _item(
                "AbbVie acquires Capstan Therapeutics",
                "Immunology pipeline expansion",
                days_ago=200,
            ),
        ],
    )
    report = build_temporal_report("ABBV", "AbbVie", timeline)
    assert "CONTEXTO 2 AÑOS" in report.two_year_summary
    assert "ÚLTIMOS 3 MESES" in report.three_month_summary
    assert "IMPACTO EN INVERSIÓN" in report.investment_impact
    assert "NARRATIVA DE MERCADO" in report.market_narrative
