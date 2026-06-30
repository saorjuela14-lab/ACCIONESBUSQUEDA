"""Temporal news analysis: 2-year backdrop, 3-month recent, ongoing issues, investment impact."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from domain.enums import NewsSentiment, NewsTopicCategory
from domain.reports import NewsItem
from providers.news.intelligence import (
    _TOPIC_LABELS,
    classify_text,
    dedupe_news,
    filter_relevant_news,
    group_by_category,
    score_from_developments,
)

_ONGOING_SIGNALS = (
    "lawsuit",
    "litigation",
    "patent",
    "investigation",
    "sec ",
    "fda",
    "recall",
    "settlement",
    "antitrust",
    "humira",
    "biosimilar",
    "deal",
    "acquisition",
    "trial",
    "phase 3",
    "approval",
)


@dataclass
class NewsTimeline:
    recent_3m: list[NewsItem] = field(default_factory=list)
    historical_2y: list[NewsItem] = field(default_factory=list)
    supplemental: list[NewsItem] = field(default_factory=list)

    @property
    def all_items(self) -> list[NewsItem]:
        return dedupe_news(self.recent_3m + self.historical_2y + self.supplemental)


@dataclass
class TemporalNewsReport:
    two_year_summary: str
    three_month_summary: str
    ongoing_issues: str
    market_narrative: str
    investment_impact: str
    news_score: float
    sentiment_avg_recent: float
    sentiment_label: str
    ongoing_themes: list[str] = field(default_factory=list)
    recent_by_topic: dict[str, list[str]] = field(default_factory=dict)
    historical_by_topic: dict[str, list[str]] = field(default_factory=dict)


def _avg_sentiment(items: list[NewsItem]) -> float:
    if not items:
        return 0.0
    scores: list[float] = []
    for item in items:
        if item.sentiment == NewsSentiment.BULLISH:
            scores.append(0.35)
        elif item.sentiment == NewsSentiment.BEARISH:
            scores.append(-0.35)
        else:
            scores.append(0.0)
    return sum(scores) / len(scores)


def _sentiment_label(avg: float) -> str:
    if avg >= 0.12:
        return "predominantly positive"
    if avg <= -0.12:
        return "predominantly negative"
    return "mixed / neutral"


def _top_events_by_topic(items: list[NewsItem], max_per_topic: int = 2) -> dict[NewsTopicCategory, list[NewsItem]]:
    grouped = group_by_category(items)
    priority = [
        NewsTopicCategory.MERGERS_ACQUISITIONS,
        NewsTopicCategory.REGULATORY,
        NewsTopicCategory.LITIGATION,
        NewsTopicCategory.PRODUCT_PIPELINE,
        NewsTopicCategory.EARNINGS,
        NewsTopicCategory.MANAGEMENT,
        NewsTopicCategory.STRATEGIC,
    ]
    result: dict[NewsTopicCategory, list[NewsItem]] = {}
    for cat in priority:
        if grouped.get(cat):
            result[cat] = grouped[cat][:max_per_topic]
    return result


def _format_event(item: NewsItem) -> str:
    date = item.published_at.strftime("%Y-%m-%d") if item.published_at else "undated"
    body = item.snippet[:180].strip() if item.snippet else item.title
    return f"[{date}] {body}"


def _extract_themes(text: str) -> set[str]:
    lower = text.lower()
    return {sig for sig in _ONGOING_SIGNALS if sig in lower}


def detect_ongoing_issues(
    recent: list[NewsItem], historical: list[NewsItem]
) -> tuple[list[str], str]:
    """Find problems/themes from 2y ago that still appear in last 3 months."""
    historical_themes: dict[str, list[NewsItem]] = {}
    for item in historical:
        text = f"{item.title} {item.snippet or ''}"
        for theme in _extract_themes(text):
            historical_themes.setdefault(theme, []).append(item)

    recent_themes: set[str] = set()
    for item in recent:
        text = f"{item.title} {item.snippet or ''}"
        recent_themes.update(_extract_themes(text))

    ongoing: list[str] = []
    for theme, old_items in historical_themes.items():
        if theme not in recent_themes:
            continue
        old_headline = old_items[0].title[:100]
        recent_match = next(
            (r for r in recent if theme in f"{r.title} {r.snippet or ''}".lower()),
            None,
        )
        if recent_match:
            ongoing.append(
                f"{theme.upper()}: issue first surfaced in coverage ('{old_headline}') "
                f"and remains active today ('{recent_match.title[:100]}')."
            )

    if not ongoing:
        narrative = (
            "No clear evidence that major problems from the 2-year lookback are still dominating "
            "headlines in the last 3 months."
        )
    else:
        narrative = "Legacy issues still in the news: " + " ".join(ongoing[:4])

    return ongoing, narrative


def build_two_year_summary(ticker: str, company_name: str, historical: list[NewsItem]) -> str:
    if not historical:
        return (
            f"2-YEAR BACKDROP ({company_name}/{ticker}): Limited historical news in data feed. "
            "Supplement with filings and earnings transcripts for full context."
        )

    events = _top_events_by_topic(historical, max_per_topic=3)
    parts = [f"2-YEAR BACKDROP — main developments for {company_name} ({ticker}):"]
    for category, items in events.items():
        label = _TOPIC_LABELS[category]
        highlights = "; ".join(_format_event(i) for i in items)
        parts.append(f"{label}: {highlights}")

    return " ".join(parts)


def build_three_month_summary(ticker: str, company_name: str, recent: list[NewsItem]) -> str:
    if not recent:
        return f"LAST 3 MONTHS ({company_name}/{ticker}): No material recent news captured."

    events = _top_events_by_topic(recent, max_per_topic=3)
    parts = [f"LAST 3 MONTHS — what is happening now with {company_name} ({ticker}):"]
    for category, items in events.items():
        label = _TOPIC_LABELS[category]
        highlights = "; ".join(_format_event(i) for i in items)
        parts.append(f"{label}: {highlights}")

    bullish = sum(1 for i in recent if i.sentiment == NewsSentiment.BULLISH)
    bearish = sum(1 for i in recent if i.sentiment == NewsSentiment.BEARISH)
    parts.append(f"Sentiment mix (3m): {bullish} bullish, {bearish} bearish, {len(recent) - bullish - bearish} neutral articles.")

    return " ".join(parts)


def build_market_narrative(recent: list[NewsItem], sentiment_avg: float) -> str:
    if not recent:
        return "MARKET NARRATIVE: Insufficient recent coverage to gauge consensus opinion."

    label = _sentiment_label(sentiment_avg)
    themes: list[str] = []
    for item in recent[:8]:
        text = item.snippet or item.title
        if len(text) > 40:
            themes.append(text[:160])

    circulating = "; ".join(themes[:4]) if themes else "; ".join(i.title for i in recent[:4])
    return (
        f"MARKET NARRATIVE: Coverage is {label} (avg sentiment {sentiment_avg:+.2f}). "
        f"What is circulating: {circulating}"
    )


def build_investment_impact(
    recent: list[NewsItem],
    historical: list[NewsItem],
    ongoing_narrative: str,
    sentiment_avg: float,
    grouped_recent: dict[NewsTopicCategory, list[NewsItem]],
) -> tuple[str, float]:
    """Link news flow to buy/sell implications and compute directional score."""
    base_score = score_from_developments(grouped_recent)

    # Weight historical context
    hist_grouped = group_by_category(historical)
    historical_score = score_from_developments(hist_grouped) * 0.35
    recent_score = base_score * 0.65
    combined = max(-100.0, min(100.0, recent_score + historical_score + sentiment_avg * 40))

    tailwinds: list[str] = []
    headwinds: list[str] = []

    for cat in (NewsTopicCategory.REGULATORY, NewsTopicCategory.PRODUCT_PIPELINE, NewsTopicCategory.MERGERS_ACQUISITIONS):
        if grouped_recent.get(cat):
            tailwinds.append(f"recent {cat.value.replace('_', ' ')}")

    if grouped_recent.get(NewsTopicCategory.LITIGATION):
        headwinds.append("active litigation coverage")
    if "still active today" in ongoing_narrative.lower() or "legacy issues" in ongoing_narrative.lower():
        headwinds.append("unresolved legacy issues still in headlines")

    bearish_recent = sum(1 for i in recent if i.sentiment == NewsSentiment.BEARISH)
    bullish_recent = sum(1 for i in recent if i.sentiment == NewsSentiment.BULLISH)
    if bearish_recent > bullish_recent + 2:
        headwinds.append("negative sentiment dominating recent flow")
    elif bullish_recent > bearish_recent + 2:
        tailwinds.append("positive sentiment dominating recent flow")

    if combined >= 15:
        stance = "news flow SUPPORTS a constructive/buy bias"
    elif combined <= -15:
        stance = "news flow CAUTIONS against buying — elevated headline risk"
    else:
        stance = "news flow is NEUTRAL for the buy decision — not a primary driver"

    impact = (
        f"INVESTMENT IMPACT: {stance} (news score {combined:+.1f}/100). "
        f"Tailwinds: {', '.join(tailwinds) or 'none identified'}. "
        f"Headwinds: {', '.join(headwinds) or 'none identified'}. "
        f"Use alongside fundamentals, valuation, and technicals — news alone does not justify a full position."
    )
    return impact, combined


def build_temporal_report(
    ticker: str,
    company_name: str,
    timeline: NewsTimeline,
) -> TemporalNewsReport:
    # Partition all items if windows are empty
    all_items = timeline.all_items
    recent = list(timeline.recent_3m)
    historical = list(timeline.historical_2y)

    if not recent and not historical and all_items:
        from providers.news.intelligence import partition_news_by_age

        recent, historical = partition_news_by_age(all_items)

    recent = filter_relevant_news(recent, ticker, company_name)
    historical = filter_relevant_news(historical, ticker, company_name)

    if not recent and timeline.recent_3m:
        recent = timeline.recent_3m[:20]
    if not historical and timeline.historical_2y:
        historical = timeline.historical_2y[:25]
    if not recent and all_items:
        from providers.news.intelligence import partition_news_by_age

        recent, _ = partition_news_by_age(all_items)
        recent = recent[:20]

    sentiment_avg = _avg_sentiment(recent)
    ongoing_themes, ongoing_narrative = detect_ongoing_issues(recent, historical)
    grouped_recent = group_by_category(recent)

    two_year = build_two_year_summary(ticker, company_name, historical)
    three_month = build_three_month_summary(ticker, company_name, recent)
    narrative = build_market_narrative(recent, sentiment_avg)
    impact, score = build_investment_impact(
        recent, historical, ongoing_narrative, sentiment_avg, grouped_recent
    )

    return TemporalNewsReport(
        two_year_summary=two_year,
        three_month_summary=three_month,
        ongoing_issues=ongoing_narrative,
        market_narrative=narrative,
        investment_impact=impact,
        news_score=score,
        sentiment_avg_recent=sentiment_avg,
        sentiment_label=_sentiment_label(sentiment_avg),
        ongoing_themes=ongoing_themes,
        recent_by_topic={
            cat.value: [i.title for i in items[:5]]
            for cat, items in grouped_recent.items()
            if items
        },
        historical_by_topic={
            cat.value: [i.title for i in items[:5]]
            for cat, items in group_by_category(historical).items()
            if items
        },
    )


def build_full_summary(report: TemporalNewsReport) -> str:
    return " | ".join(
        [
            report.two_year_summary,
            report.three_month_summary,
            report.ongoing_issues,
            report.market_narrative,
            report.investment_impact,
        ]
    )
