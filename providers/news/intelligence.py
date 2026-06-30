"""Company news intelligence: categorization, query planning, and narrative synthesis."""

from __future__ import annotations

from domain.enums import ImpactLevel, NewsSentiment, NewsTopicCategory, TimeHorizon
from domain.reports import NewsItem

_PHARMA_SECTORS = {"healthcare", "health care"}
_PHARMA_INDUSTRIES = {
    "drug manufacturers",
    "drug manufacturers—general",
    "drug manufacturers—specialty & generic",
    "biotechnology",
    "medical devices",
    "diagnostics & research",
}

_TOPIC_KEYWORDS: dict[NewsTopicCategory, tuple[str, ...]] = {
    NewsTopicCategory.MERGERS_ACQUISITIONS: (
        "acquisition",
        "acquire",
        "acquired",
        "merger",
        "merge",
        "buyout",
        "takeover",
        "deal to buy",
        "purchase",
        "m&a",
    ),
    NewsTopicCategory.LITIGATION: (
        "lawsuit",
        "litigation",
        "sued",
        "suing",
        "settlement",
        "trial",
        "verdict",
        "patent dispute",
        "antitrust",
        "class action",
        "indictment",
        "fine",
        "penalty",
    ),
    NewsTopicCategory.REGULATORY: (
        "fda",
        "approval",
        "approved",
        "approves",
        "reject",
        "rejected",
        "regulatory",
        "sec ",
        "investigation",
        "subpoena",
        "warning letter",
        "ema",
        "regulator",
    ),
    NewsTopicCategory.PRODUCT_PIPELINE: (
        "clinical trial",
        "phase 1",
        "phase 2",
        "phase 3",
        "phase i",
        "phase ii",
        "phase iii",
        "pipeline",
        "drug",
        "therapy",
        "treatment",
        "indication",
        "launch",
        "rollout",
        "product",
        "device",
        "platform",
    ),
    NewsTopicCategory.EARNINGS: (
        "earnings",
        "revenue",
        "profit",
        "guidance",
        "quarter",
        "q1",
        "q2",
        "q3",
        "q4",
        "beat",
        "miss",
        "eps",
        "outlook",
        "forecast",
    ),
    NewsTopicCategory.MANAGEMENT: (
        "ceo",
        "cfo",
        "executive",
        "resign",
        "resignation",
        "appoint",
        "appointed",
        "board",
        "leadership",
        "chief ",
    ),
    NewsTopicCategory.STRATEGIC: (
        "partnership",
        "joint venture",
        "restructuring",
        "spin-off",
        "spinoff",
        "layoff",
        "job cuts",
        "expansion",
        "strategy",
        "reorganization",
    ),
}

_TOPIC_LABELS: dict[NewsTopicCategory, str] = {
    NewsTopicCategory.MERGERS_ACQUISITIONS: "M&A and acquisitions",
    NewsTopicCategory.LITIGATION: "Litigation and legal",
    NewsTopicCategory.REGULATORY: "Regulatory and approvals",
    NewsTopicCategory.PRODUCT_PIPELINE: "Products and pipeline",
    NewsTopicCategory.EARNINGS: "Earnings and guidance",
    NewsTopicCategory.MANAGEMENT: "Management and leadership",
    NewsTopicCategory.STRATEGIC: "Strategic moves",
    NewsTopicCategory.GENERAL: "General coverage",
}

_POSITIVE = {"beat", "surge", "rally", "upgrade", "growth", "profit", "buy", "bullish", "record", "approved", "approval"}
_NEGATIVE = {
    "miss",
    "decline",
    "drop",
    "loss",
    "downgrade",
    "sell",
    "bearish",
    "lawsuit",
    "investigation",
    "reject",
    "rejected",
    "recall",
    "layoff",
    "fine",
}


def is_pharma_sector(sector: str | None, industry: str | None) -> bool:
    sector_l = (sector or "").lower()
    industry_l = (industry or "").lower()
    if sector_l in _PHARMA_SECTORS:
        return True
    return any(token in industry_l for token in _PHARMA_INDUSTRIES) or "pharma" in industry_l


def build_intelligence_queries(
    ticker: str,
    company_name: str,
    sector: str | None = None,
    industry: str | None = None,
) -> list[tuple[NewsTopicCategory, str]]:
    name = company_name or ticker
    queries: list[tuple[NewsTopicCategory, str]] = [
        (NewsTopicCategory.MERGERS_ACQUISITIONS, f"{name} acquisition merger acquire company deal"),
        (NewsTopicCategory.LITIGATION, f"{ticker} {name} lawsuit litigation settlement legal"),
        (NewsTopicCategory.EARNINGS, f"{name} earnings results guidance quarterly"),
        (NewsTopicCategory.MANAGEMENT, f"{name} CEO executive leadership board change"),
        (NewsTopicCategory.STRATEGIC, f"{name} partnership restructuring strategy spin-off"),
        (NewsTopicCategory.GENERAL, f"{ticker} {name} company news developments"),
    ]

    if is_pharma_sector(sector, industry):
        queries.extend(
            [
                (NewsTopicCategory.REGULATORY, f"{name} FDA approval drug regulatory decision"),
                (NewsTopicCategory.PRODUCT_PIPELINE, f"{name} clinical trial phase drug pipeline therapy"),
                (NewsTopicCategory.LITIGATION, f"{name} patent litigation humira skyrizi rinvoq"),
            ]
        )
    else:
        queries.append((NewsTopicCategory.REGULATORY, f"{ticker} {name} regulatory SEC investigation approval"))
        queries.append((NewsTopicCategory.PRODUCT_PIPELINE, f"{name} product launch pipeline innovation"))

    return queries


def classify_text(text: str) -> NewsTopicCategory:
    lower = text.lower()
    scores: dict[NewsTopicCategory, int] = {cat: 0 for cat in NewsTopicCategory}
    for category, keywords in _TOPIC_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lower:
                scores[category] += 1
    best = max(scores.items(), key=lambda item: item[1])
    if best[1] == 0:
        return NewsTopicCategory.GENERAL
    return best[0]


def classify_sentiment(text: str) -> NewsSentiment:
    lower = text.lower()
    pos = sum(1 for w in _POSITIVE if w in lower)
    neg = sum(1 for w in _NEGATIVE if w in lower)
    if pos > neg:
        return NewsSentiment.BULLISH
    if neg > pos:
        return NewsSentiment.BEARISH
    return NewsSentiment.NEUTRAL


def impact_for_category(category: NewsTopicCategory, sentiment: NewsSentiment) -> ImpactLevel:
    if category in {NewsTopicCategory.MERGERS_ACQUISITIONS, NewsTopicCategory.REGULATORY, NewsTopicCategory.LITIGATION}:
        return ImpactLevel.HIGH
    if category in {NewsTopicCategory.PRODUCT_PIPELINE, NewsTopicCategory.EARNINGS, NewsTopicCategory.MANAGEMENT}:
        return ImpactLevel.MEDIUM
    if sentiment != NewsSentiment.NEUTRAL:
        return ImpactLevel.MEDIUM
    return ImpactLevel.LOW


def enrich_news_item(item: NewsItem, hint_category: NewsTopicCategory | None = None) -> NewsItem:
    text = f"{item.title} {item.snippet or ''}"
    category = hint_category or classify_text(text)
    sentiment = classify_sentiment(text)
    return item.model_copy(
        update={
            "category": category,
            "sentiment": sentiment,
            "impact": impact_for_category(category, sentiment),
            "horizon": TimeHorizon.MONTHLY if category != NewsTopicCategory.EARNINGS else TimeHorizon.WEEKLY,
        }
    )


def dedupe_news(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = item.title.lower().strip()[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def group_by_category(items: list[NewsItem]) -> dict[NewsTopicCategory, list[NewsItem]]:
    grouped: dict[NewsTopicCategory, list[NewsItem]] = {cat: [] for cat in NewsTopicCategory}
    for item in items:
        grouped[item.category].append(item)
    return grouped


def _format_item(item: NewsItem) -> str:
    date = ""
    if item.published_at:
        date = f" ({item.published_at.strftime('%Y-%m-%d')})"
    snippet = ""
    if item.snippet and item.snippet.lower() not in item.title.lower():
        snippet = f" — {item.snippet[:120].strip()}"
    return f"{item.title}{date}{snippet}"


def build_actualidad_summary(
    ticker: str,
    company_name: str,
    grouped: dict[NewsTopicCategory, list[NewsItem]],
) -> str:
    """Narrative summary of recent company developments by topic."""
    name = company_name or ticker
    sections: list[str] = [f"Company news intelligence for {name} ({ticker}):"]

    priority_order = [
        NewsTopicCategory.REGULATORY,
        NewsTopicCategory.PRODUCT_PIPELINE,
        NewsTopicCategory.MERGERS_ACQUISITIONS,
        NewsTopicCategory.LITIGATION,
        NewsTopicCategory.EARNINGS,
        NewsTopicCategory.MANAGEMENT,
        NewsTopicCategory.STRATEGIC,
        NewsTopicCategory.GENERAL,
    ]

    covered = 0
    for category in priority_order:
        items = grouped.get(category, [])
        label = _TOPIC_LABELS[category]
        if not items:
            sections.append(f"{label}: no recent coverage found in scan.")
            continue
        covered += 1
        highlights = "; ".join(_format_item(item) for item in items[:3])
        sections.append(f"{label}: {highlights}")

    if covered == 0:
        sections.append("No material company developments detected in the current news scan.")

    return " ".join(sections)


def score_from_developments(grouped: dict[NewsTopicCategory, list[NewsItem]]) -> float:
    score = 0.0
    weights = {
        NewsTopicCategory.REGULATORY: 12.0,
        NewsTopicCategory.PRODUCT_PIPELINE: 8.0,
        NewsTopicCategory.MERGERS_ACQUISITIONS: 6.0,
        NewsTopicCategory.EARNINGS: 5.0,
        NewsTopicCategory.MANAGEMENT: 3.0,
        NewsTopicCategory.STRATEGIC: 4.0,
        NewsTopicCategory.LITIGATION: -12.0,
        NewsTopicCategory.GENERAL: 1.0,
    }

    for category, items in grouped.items():
        if not items:
            continue
        base = weights.get(category, 0.0)
        for item in items[:4]:
            direction = 1.0
            if item.sentiment == NewsSentiment.BEARISH:
                direction = -1.0
            elif item.sentiment == NewsSentiment.NEUTRAL:
                direction = 0.3
            score += base * direction * 0.35

    return max(-100.0, min(100.0, score))
