"""Firm autonomy bootstrap and settings."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import Settings
from services.firm_autonomy_bootstrap import ensure_firm_autonomy_flags


def test_firm_autonomy_defaults_on():
    assert Settings.model_fields["firm_autonomy"].default is True
    assert Settings.model_fields["auto_execute_trades"].default is True
    assert Settings.model_fields["auto_execute_live"].default is True
    assert Settings.model_fields["auto_execute_paper_first"].default is False
    assert Settings.model_fields["autopilot_interval_minutes"].default == 30


def test_effective_autopilot_interval_fallback():
    s = Settings.model_construct(firm_autonomy=True, autopilot_interval_minutes=0)
    assert s.effective_autopilot_interval_minutes == 30
    s2 = Settings.model_construct(firm_autonomy=True, autopilot_interval_minutes=15)
    assert s2.effective_autopilot_interval_minutes == 15
    s3 = Settings.model_construct(firm_autonomy=False, autopilot_interval_minutes=0)
    assert s3.effective_autopilot_interval_minutes == 0


@pytest.mark.asyncio
async def test_bootstrap_sets_promotion_flag():
    session = MagicMock()
    repo = MagicMock()
    repo.get_json = AsyncMock(return_value={})
    repo.set_json = AsyncMock()

    with patch("services.firm_autonomy_bootstrap.OpsFlagRepository", return_value=repo), \
         patch("services.firm_autonomy_bootstrap.AuditService") as Audit:
        Audit.return_value.record = AsyncMock()
        out = await ensure_firm_autonomy_flags(session, firm_autonomy=True)
    assert out["promoted"] is True
    repo.set_json.assert_awaited()


@pytest.mark.asyncio
async def test_bootstrap_skips_when_already_promoted():
    session = MagicMock()
    repo = MagicMock()
    repo.get_json = AsyncMock(return_value={"promoted": True, "note": "existing"})
    repo.set_json = AsyncMock()

    with patch("services.firm_autonomy_bootstrap.OpsFlagRepository", return_value=repo):
        out = await ensure_firm_autonomy_flags(session, firm_autonomy=True)
    assert out["promoted"] is True
    repo.set_json.assert_not_awaited()
