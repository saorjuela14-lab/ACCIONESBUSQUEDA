"""Multi-asset beta desks — gold / forex / crypto paper module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from domain.multiasset import MultiAssetOrderRequest
from domain.reports import AgentReport
from services.multiasset.desk_service import MultiAssetDeskService
from services.multiasset.desks import DESKS, desk_symbols, get_desk


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_desks_catalog_independent():
    assert set(DESKS) == {"gold", "forex", "crypto"}
    gold = get_desk("gold")
    fx = get_desk("forex")
    crypto = get_desk("crypto")
    assert "GLD" in desk_symbols("gold")
    assert "UUP" in desk_symbols("forex")
    assert "BTC/USD" in desk_symbols("crypto")
    assert gold.agent_names != fx.agent_names != crypto.agent_names
    assert crypto.time_in_force == "gtc"
    assert gold.time_in_force == "day"
    assert len(gold.agent_names) == 3
    assert len(fx.agent_names) == 3
    assert len(crypto.agent_names) == 3


def test_list_desks_payload():
    desks = MultiAssetDeskService().list_desks()
    assert len(desks) == 3
    names = {d["desk"] for d in desks}
    assert names == {"gold", "forex", "crypto"}


@pytest.mark.asyncio
async def test_execute_dry_run_and_history(session: AsyncSession, monkeypatch):
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    from config.settings import get_settings

    get_settings.cache_clear()

    mock_broker = MagicMock()
    mock_broker.is_configured.return_value = False
    with patch("services.multiasset.desk_service.get_beta_broker_provider", return_value=mock_broker):
        svc = MultiAssetDeskService(session)
        result = await svc.execute(
            MultiAssetOrderRequest(
                desk="gold",
                symbol="GLD",
                side="buy",
                qty=1,
                dry_run=True,
                confirm=False,
                note="test dry",
            )
        )
        assert result.ok is True
        assert result.dry_run is True
        assert result.status == "dry_run"
        assert result.symbol == "GLD"

        hist = await svc.history(desk="gold", limit=10)
        assert len(hist) == 1
        assert hist[0].symbol == "GLD"
        assert hist[0].side == "buy"
        assert hist[0].status == "dry_run"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_execute_rejects_unknown_symbol(session: AsyncSession, monkeypatch):
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    from config.settings import get_settings

    get_settings.cache_clear()
    mock_broker = MagicMock()
    mock_broker.is_configured.return_value = False
    with patch("services.multiasset.desk_service.get_beta_broker_provider", return_value=mock_broker):
        svc = MultiAssetDeskService(session)
        with pytest.raises(ValueError, match="universo"):
            await svc.execute(
                MultiAssetOrderRequest(
                    desk="crypto",
                    symbol="AAPL",
                    side="buy",
                    notional=25,
                    dry_run=True,
                )
            )
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_brief_aggregates_specialized_agents(monkeypatch):
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    from config.settings import get_settings

    get_settings.cache_clear()

    class _Stub:
        def __init__(self, name: str):
            self.name = name

        async def analyze(self, ticker: str, **kwargs):
            return AgentReport(
                agent_name=self.name,
                ticker=ticker,
                score=20.0,
                confidence=0.6,
                summary=f"{self.name} ok",
            )

    stubs = [
        _Stub("gold_macro_agent"),
        _Stub("gold_technical_agent"),
        _Stub("gold_flow_agent"),
    ]
    mock_broker = MagicMock()
    mock_broker.is_configured.return_value = False

    with (
        patch("services.multiasset.desk_service.get_beta_broker_provider", return_value=mock_broker),
        patch("services.multiasset.desk_service.build_agents", return_value=stubs),
        patch(
            "services.multiasset.desk_service.quote_symbol",
            AsyncMock(return_value={"symbol": "GLD", "current_price": 180.0}),
        ),
    ):
        brief = await MultiAssetDeskService().brief("gold", "GLD")
        assert brief.desk == "gold"
        assert brief.symbol == "GLD"
        assert brief.recommendation == "buy"
        assert len(brief.votes) == 3
        assert all("gold_" in v.agent_name for v in brief.votes)

    get_settings.cache_clear()


def test_client_forbidden_includes_beta_api():
    from apis.middleware.access_auth import CLIENT_FORBIDDEN_PREFIXES

    assert "/api/v1/beta" in CLIENT_FORBIDDEN_PREFIXES
