"""Capital-aware share-price policy — keep % allocations, pick affordable names."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapitalPricePolicy:
    """Guides ticker selection so positions fit the portfolio cash."""

    capital: float
    tier: str  # micro | small | medium | standard
    max_share_price: float | None
    prefer_max_price: float
    min_share_price: float
    prefer_whole_shares: bool
    target_positions: int
    description_es: str

    @property
    def avg_line_usd(self) -> float:
        return self.capital * 0.9 / max(self.target_positions, 1)


def capital_price_policy(capital: float, target_positions: int = 4) -> CapitalPricePolicy:
    """
    Derive price bands from total capital.

    Micro (~$50): prioritize penny / ultra-low price stocks so whole shares fit.
    Small / medium: soft max price near average line size.
    Standard: no hard max — CFDs still available as fallback.
    """
    capital = max(1.0, float(capital))
    n = max(2, min(6, int(target_positions)))
    avg_line = capital * 0.9 / n

    if capital <= 100:
        # $50 portfolio → aim for stocks under ~$5 (penny / micro)
        max_price = min(5.0, max(1.0, avg_line * 1.2))
        prefer = min(max_price, avg_line)
        return CapitalPricePolicy(
            capital=capital,
            tier="micro",
            max_share_price=max_price,
            prefer_max_price=prefer,
            min_share_price=0.05,
            prefer_whole_shares=True,
            target_positions=n,
            description_es=(
                f"Capital micro (${capital:,.0f}): buscar penny stocks / acciones ≤ ${max_price:.2f} "
                f"para comprar acciones enteras (~${avg_line:.0f} por posición)."
            ),
        )

    if capital <= 500:
        max_price = min(25.0, max(5.0, avg_line * 1.5))
        return CapitalPricePolicy(
            capital=capital,
            tier="small",
            max_share_price=max_price,
            prefer_max_price=min(max_price, avg_line),
            min_share_price=0.25,
            prefer_whole_shares=True,
            target_positions=n,
            description_es=(
                f"Capital pequeño (${capital:,.0f}): priorizar acciones ≤ ${max_price:.2f} "
                f"(línea típica ~${avg_line:.0f})."
            ),
        )

    if capital <= 2000:
        max_price = min(80.0, max(15.0, avg_line * 1.8))
        return CapitalPricePolicy(
            capital=capital,
            tier="medium",
            max_share_price=max_price,
            prefer_max_price=min(max_price, avg_line),
            min_share_price=1.0,
            prefer_whole_shares=True,
            target_positions=n,
            description_es=(
                f"Capital medio (${capital:,.0f}): preferir acciones ≤ ${max_price:.2f} "
                f"para mantener proporciones con acciones enteras."
            ),
        )

    return CapitalPricePolicy(
        capital=capital,
        tier="standard",
        max_share_price=None,
        prefer_max_price=avg_line,
        min_share_price=1.0,
        prefer_whole_shares=False,
        target_positions=n,
        description_es=(
            f"Capital estándar (${capital:,.0f}): sin tope duro de precio; "
            f"se prefieren nombres que quepan en ~${avg_line:,.0f} por línea."
        ),
    )


def price_fits_hard(price: float, policy: CapitalPricePolicy) -> bool:
    """Hard filter: drop names outside min/max when a max is set."""
    if price <= 0:
        return False
    if price < policy.min_share_price:
        return False
    if policy.max_share_price is not None and price > policy.max_share_price:
        return False
    return True


def price_fits_line(price: float, line_usd: float, policy: CapitalPricePolicy) -> bool:
    """Whether at least 1 whole share fits in this allocation line."""
    if price <= 0 or line_usd <= 0:
        return False
    if policy.prefer_whole_shares:
        return price <= line_usd
    if policy.max_share_price is not None:
        return price <= policy.max_share_price
    return True


def affordability_bonus(price: float, line_usd: float, policy: CapitalPricePolicy) -> float:
    """
    Score boost for names that fit the capital band.
    Higher = better for ranking among otherwise equal candidates.
    """
    if price <= 0:
        return -50.0

    bonus = 0.0
    if policy.min_share_price <= price and (
        policy.max_share_price is None or price <= policy.max_share_price
    ):
        bonus += 15.0
    elif policy.max_share_price is not None and price > policy.max_share_price:
        bonus -= 25.0

    if line_usd > 0 and price <= line_usd:
        # Whole-share fit
        bonus += 20.0
        shares = int(line_usd // price)
        if shares >= 2:
            bonus += 5.0
    elif line_usd > 0 and price > line_usd:
        # Needs CFD / fractional for this line
        if policy.prefer_whole_shares:
            bonus -= 15.0
        else:
            bonus -= 5.0

    if policy.tier == "micro" and price <= 5.0:
        bonus += 10.0  # explicit penny preference
    if policy.tier == "micro" and price <= 2.0:
        bonus += 5.0

    if price <= policy.prefer_max_price:
        bonus += 8.0

    return bonus


def discovery_themes_for_capital(policy: CapitalPricePolicy, base_themes: list[str] | None = None) -> list[str]:
    """Augment discovery themes so social/news scan finds affordable names."""
    themes = list(base_themes or [])
    if policy.tier == "micro":
        themes.extend([
            "penny stocks under $5 breakout",
            "micro cap volume spike under $5",
            "OTC and low priced stocks momentum",
            "small float penny stock catalyst",
        ])
    elif policy.tier == "small":
        themes.extend([
            "stocks under $20 growth",
            "small cap under $25 breakout",
            "affordable growth stocks under $25",
        ])
    elif policy.tier == "medium":
        themes.extend([
            "stocks under $80 mid cap growth",
            "reasonably priced growth stocks",
        ])
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in themes:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out
