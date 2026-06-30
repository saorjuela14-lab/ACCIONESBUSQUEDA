"""News intelligence unit tests."""

from domain.enums import NewsSentiment, NewsTopicCategory
from domain.reports import NewsItem
from providers.news.intelligence import (
    build_actualidad_summary,
    build_intelligence_queries,
    classify_text,
    dedupe_news,
    enrich_news_item,
    group_by_category,
    is_pharma_sector,
    score_from_developments,
)


def test_is_pharma_sector():
    assert is_pharma_sector("Healthcare", "Drug Manufacturers—General") is True
    assert is_pharma_sector("Technology", "Software") is False


def test_pharma_queries_include_fda_and_pipeline():
    queries = build_intelligence_queries("ABBV", "AbbVie Inc.", "Healthcare", "Drug Manufacturers—General")
    categories = {cat for cat, _ in queries}
    assert NewsTopicCategory.REGULATORY in categories
    assert NewsTopicCategory.PRODUCT_PIPELINE in categories
    assert NewsTopicCategory.MERGERS_ACQUISITIONS in categories


def test_classify_acquisition():
    assert classify_text("AbbVie to acquire ImmunoGen in $10B deal") == NewsTopicCategory.MERGERS_ACQUISITIONS


def test_classify_fda_approval():
    assert classify_text("FDA approves new AbbVie rheumatoid arthritis drug") == NewsTopicCategory.REGULATORY


def test_classify_lawsuit():
    assert classify_text("AbbVie faces patent litigation over Humira biosimilars") == NewsTopicCategory.LITIGATION


def test_build_actualidad_summary_groups_topics():
    items = [
        enrich_news_item(
            NewsItem(title="FDA approves Skyrizi for new indication", source="Reuters"),
            hint_category=NewsTopicCategory.REGULATORY,
        ),
        enrich_news_item(
            NewsItem(title="AbbVie acquires biotech startup", source="Bloomberg"),
            hint_category=NewsTopicCategory.MERGERS_ACQUISITIONS,
        ),
    ]
    grouped = group_by_category(items)
    summary = build_actualidad_summary("ABBV", "AbbVie", grouped)
    assert "AbbVie" in summary
    assert "FDA" in summary or "Regulatory" in summary
    assert "acquisition" in summary.lower() or "M&A" in summary


def test_dedupe_news():
    items = [
        NewsItem(title="Same headline", source="a"),
        NewsItem(title="Same headline", source="b"),
        NewsItem(title="Different headline", source="c"),
    ]
    assert len(dedupe_news(items)) == 2


def test_score_from_developments_balances_risks():
    grouped = {
        NewsTopicCategory.REGULATORY: [
            NewsItem(
                title="FDA approval",
                source="x",
                category=NewsTopicCategory.REGULATORY,
                sentiment=NewsSentiment.BULLISH,
            )
        ],
        NewsTopicCategory.LITIGATION: [
            NewsItem(
                title="Major lawsuit filed",
                source="y",
                category=NewsTopicCategory.LITIGATION,
                sentiment=NewsSentiment.BEARISH,
            )
        ],
        NewsTopicCategory.GENERAL: [],
        NewsTopicCategory.MERGERS_ACQUISITIONS: [],
        NewsTopicCategory.PRODUCT_PIPELINE: [],
        NewsTopicCategory.EARNINGS: [],
        NewsTopicCategory.MANAGEMENT: [],
        NewsTopicCategory.STRATEGIC: [],
    }
    score = score_from_developments(grouped)
    assert -100 <= score <= 100
