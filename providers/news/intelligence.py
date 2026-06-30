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
    NewsTopicCategory.MERGERS_ACQUISITIONS: "M&A y adquisiciones",
    NewsTopicCategory.LITIGATION: "Demandas y litigios",
    NewsTopicCategory.REGULATORY: "Regulatorio y aprobaciones (FDA/EMA)",
    NewsTopicCategory.PRODUCT_PIPELINE: "Productos y pipeline",
    NewsTopicCategory.EARNINGS: "Resultados y guidance",
    NewsTopicCategory.MANAGEMENT: "Management y liderazgo",
    NewsTopicCategory.STRATEGIC: "Movimientos estratégicos",
    NewsTopicCategory.GENERAL: "Cobertura general",
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

_FINANCE_SITES = "site:reuters.com OR site:bloomberg.com OR site:finance.yahoo.com OR site:seekingalpha.com"
_PHARMA_SITES = "site:fiercepharma.com OR site:biopharmadive.com OR site:statnews.com"

_LOW_QUALITY_URL_FRAGMENTS = (
    "wikipedia.org",
    "/investors",
    "/investor-relations",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "glassdoor.com",
    "indeed.com",
)

_LOW_QUALITY_TITLE_FRAGMENTS = (
    "welcome to",
    "official site",
    "careers at",
    "investor relations",
    "stock quote",
    "stock price",
)

_NOISE_TITLE_FRAGMENTS = (
    "best stocks to buy",
    "no-brainer",
    "stocks to buy right now",
    "rockets ",
    "52-week high",
    "winning streak",
    "dividend stocks to buy",
    "valuation check",
)


def _short_company_name(company_name: str) -> str:
    for suffix in (" Inc.", " Inc", " Corp.", " Corp", " Ltd.", " Ltd", " PLC", " Co."):
        if company_name.endswith(suffix):
            return company_name[: -len(suffix)].strip()
    return company_name.strip()


def partition_news_by_age(items: list[NewsItem], recent_days: int = 90) -> tuple[list[NewsItem], list[NewsItem]]:
    """Split news into recent window and older backdrop."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=recent_days)
    recent: list[NewsItem] = []
    historical: list[NewsItem] = []
    undated: list[NewsItem] = []

    for item in items:
        if item.published_at is None:
            undated.append(item)
            continue
        pub = item.published_at if item.published_at.tzinfo else item.published_at.replace(tzinfo=timezone.utc)
        if pub >= cutoff:
            recent.append(item)
        else:
            historical.append(item)

    # Undated feed items are treated as recent supplemental coverage
    recent.extend(undated)
    return recent, historical


def is_relevant_news_item(item: NewsItem, ticker: str, company_name: str) -> bool:
    text = f"{item.title} {item.snippet or ''} {item.url or ''}".lower()
    url = (item.url or "").lower()
    title = item.title.lower()

    if any(fragment in url for fragment in _LOW_QUALITY_URL_FRAGMENTS):
        return False
    if any(fragment in title for fragment in _LOW_QUALITY_TITLE_FRAGMENTS):
        return False
    if any(fragment in title for fragment in _NOISE_TITLE_FRAGMENTS) and not item.snippet:
        return False
    if title.strip() in {ticker.lower(), company_name.lower(), _short_company_name(company_name).lower()}:
        return False

    short_name = _short_company_name(company_name).lower()
    tokens = {ticker.lower(), short_name.lower(), company_name.lower()}
    if short_name:
        tokens.add(short_name.split()[0].lower())

    if not any(token and token in text for token in tokens):
        return False

    if item.snippet and len(item.snippet) > 40:
        return True

    # Require some news-like signal beyond bare ticker mention
    news_signals = (
        "approval",
        "acqui",
        "merger",
        "lawsuit",
        "litigation",
        "earnings",
        "fda",
        "trial",
        "ceo",
        "guidance",
        "revenue",
        "patent",
        "settlement",
        "deal",
        "launch",
        "pipeline",
        "dividend",
        "buyback",
        "downgrade",
        "upgrade",
    )
    return any(signal in text for signal in news_signals) or item.published_at is not None


def filter_relevant_news(items: list[NewsItem], ticker: str, company_name: str) -> list[NewsItem]:
    return [item for item in items if is_relevant_news_item(item, ticker, company_name)]


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
    name = _short_company_name(company_name or ticker)
    news_sites = _FINANCE_SITES
    if is_pharma_sector(sector, industry):
        news_sites = f"{_FINANCE_SITES} OR {_PHARMA_SITES}"

    queries: list[tuple[NewsTopicCategory, str]] = [
        (NewsTopicCategory.MERGERS_ACQUISITIONS, f"({news_sites}) {ticker} {name} acquisition merger deal 2025 2026"),
        (NewsTopicCategory.LITIGATION, f"({news_sites}) {ticker} {name} lawsuit litigation settlement patent"),
        (NewsTopicCategory.EARNINGS, f"({news_sites}) {ticker} {name} earnings guidance revenue quarterly"),
        (NewsTopicCategory.MANAGEMENT, f"({news_sites}) {name} CEO executive leadership appoint resign"),
        (NewsTopicCategory.STRATEGIC, f"({news_sites}) {name} partnership restructuring spin-off strategy"),
    ]

    if is_pharma_sector(sector, industry):
        queries.extend(
            [
                (NewsTopicCategory.REGULATORY, f"({news_sites}) {name} FDA approval drug regulatory 2025 2026"),
                (NewsTopicCategory.PRODUCT_PIPELINE, f"({news_sites}) {name} clinical trial phase pipeline therapy"),
                (NewsTopicCategory.LITIGATION, f"({news_sites}) {name} humira skyrizi rinvoq patent biosimilar"),
            ]
        )
    else:
        queries.extend(
            [
                (NewsTopicCategory.REGULATORY, f"({news_sites}) {ticker} {name} regulatory SEC investigation approval"),
                (NewsTopicCategory.PRODUCT_PIPELINE, f"({news_sites}) {name} product launch pipeline innovation"),
            ]
        )

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

    if item.snippet and len(item.snippet) > 30:
        body = item.snippet[:220].strip()
        if item.title.lower() not in body.lower()[:40]:
            return f"{body}{date}"
        return f"{item.title}: {body}{date}"

    return f"{item.title}{date}"


def build_actualidad_summary(
    ticker: str,
    company_name: str,
    grouped: dict[NewsTopicCategory, list[NewsItem]],
) -> str:
    """Narrative summary of recent company developments by topic."""
    name = company_name or ticker
    sections: list[str] = [f"Actualidad reciente de {name} ({ticker}):"]

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
            continue
        covered += 1
        highlights = "; ".join(_format_item(item) for item in items[:3])
        sections.append(f"{label}: {highlights}")

    if covered == 0:
        sections.append("No se detectaron desarrollos materiales en el escaneo actual de noticias.")

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
