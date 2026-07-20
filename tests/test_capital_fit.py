"""Tests for capital-aware price policy."""

from services.capital_fit import (
    affordability_bonus,
    capital_price_policy,
    discovery_themes_for_capital,
    price_fits_hard,
)


def test_micro_capital_prefers_penny_band():
    policy = capital_price_policy(50)
    assert policy.tier == "micro"
    assert policy.max_share_price is not None
    assert policy.max_share_price <= 5.0
    assert policy.prefer_whole_shares
    assert price_fits_hard(2.5, policy)
    assert not price_fits_hard(120.0, policy)


def test_standard_capital_no_hard_max():
    policy = capital_price_policy(10000)
    assert policy.tier == "standard"
    assert policy.max_share_price is None


def test_affordability_bonus_favors_cheap_on_micro():
    policy = capital_price_policy(50)
    cheap = affordability_bonus(1.5, line_usd=12, policy=policy)
    expensive = affordability_bonus(180.0, line_usd=12, policy=policy)
    assert cheap > expensive


def test_discovery_themes_include_penny_for_micro():
    themes = discovery_themes_for_capital(capital_price_policy(50), ["biotech"])
    assert any("penny" in t.lower() for t in themes)
    assert "biotech" in themes
