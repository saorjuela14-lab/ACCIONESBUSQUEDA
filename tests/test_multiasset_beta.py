"""Multi-asset beta desks — tracking, effectiveness, and paper execute."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, MultiAssetTradeORM
from domain.multiasset import DeskBrief, MultiAssetOrderRequest
from domain.reports import AgentReport
from services.multiasset.desk_service import MultiAssetDeskService
from services.multiasset.desks import DESKS, desk_symbols, get_desk
from services.multiasset.trade_tracker import MultiAssetTradeTracker, classify_outcome


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
    assert len(gold.agent_names) == 3


def test_list_desks_payload():
    desks = MultiAssetDeskService().list_desks()
    assert len(desks) == 3
    assert {d["desk"] for d in desks} == {"gold", "forex", "crypto"}


def test_classify_outcome_feedback_tags():
    ok, tag, _ = classify_outcome("buy", 5.0, desk="gold")
    assert ok and tag == "correct_long"
    ok, tag, _ = classify_outcome("buy", -3.0, desk="gold")
    assert not ok and tag == "false_long"
    ok, tag, _ = classify_outcome("hold", 4.0, desk="gold")
    assert not ok and tag == "missed_up"


@pytest.mark.asyncio
async def test_execute_dry_run_opens_tracked_trade(session: AsyncSession, monkeypatch):
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    from config.settings import get_settings

    get_settings.cache_clear()

    mock_broker = MagicMock()
    mock_broker.is_configured.return_value = False
    brief = DeskBrief(
        desk="gold",
        symbol="GLD",
        recommendation="buy",
        confidence=0.6,
        score=18.0,
        summary="test brief",
        entry_hint=180.0,
        stop_hint=172.0,
        target_hint=194.0,
        votes=[],
    )

    with (
        patch("services.multiasset.desk_service.get_beta_broker_provider", return_value=mock_broker),
        patch(
            "services.multiasset.desk_service.quote_symbol",
            AsyncMock(return_value={"symbol": "GLD", "current_price": 180.0}),
        ),
        patch.object(MultiAssetDeskService, "brief", AsyncMock(return_value=brief)),
    ):
        svc = MultiAssetDeskService(session)
        result = await svc.execute(
            MultiAssetOrderRequest(
                desk="gold",
                symbol="GLD",
                side="buy",
                qty=1,
                dry_run=True,
                note="test dry",
            )
        )
        assert result.ok and result.dry_run
        assert result.payload.get("tracked_trade_id")

        open_t = await MultiAssetTradeTracker(session).list_open(desk="gold")
        assert len(open_t) == 1
        assert open_t[0].symbol == "GLD"
        assert open_t[0].entry_price == 180.0
        assert open_t[0].recommendation == "buy"
        assert open_t[0].is_sim is True

        # Close at higher price → win + correct_long
        with patch(
            "services.multiasset.desk_service.quote_symbol",
            AsyncMock(return_value={"symbol": "GLD", "current_price": 190.0}),
        ):
            closed_res = await svc.execute(
                MultiAssetOrderRequest(
                    desk="gold",
                    symbol="GLD",
                    side="sell",
                    qty=1,
                    dry_run=True,
                )
            )
        assert closed_res.payload.get("was_correct") is True
        assert closed_res.payload.get("error_tag") == "correct_long"

        tr = await MultiAssetTradeTracker(session).track_record(desk="gold")
        assert tr.trades_closed == 1
        assert tr.trades_wins == 1
        assert tr.briefs_evaluated == 1
        assert tr.brief_hit_rate_pct == 100.0

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_track_record_error_feedback(session: AsyncSession):
    tracker = MultiAssetTradeTracker(session)
    brief = DeskBrief(
        desk="gold",
        symbol="GLD",
        recommendation="buy",
        confidence=0.7,
        score=20,
        summary="bullish",
        votes=[],
    )
    # Attach agent scores via open then mutate — open with votes
    from domain.multiasset import AgentVote

    brief.votes = [
        AgentVote(agent_name="gold_macro_agent", label_es="Oro · Macro", score=25, confidence=0.6, summary="ok"),
        AgentVote(agent_name="gold_technical_agent", label_es="Oro · Técnico", score=15, confidence=0.5, summary="ok"),
        AgentVote(agent_name="gold_flow_agent", label_es="Oro · Flujo", score=-20, confidence=0.5, summary="bear"),
    ]
    await tracker.open_trade(
        desk="gold", symbol="GLD", qty=1, entry_price=100.0, brief=brief, is_sim=True
    )
    closed = await tracker.close_trade(desk="gold", symbol="GLD", exit_price=90.0, exit_reason="stop")
    assert closed is not None
    assert closed.was_correct is False
    assert closed.error_tag == "false_long"

    tr = await tracker.track_record(desk="gold")
    assert tr.trades_losses == 1
    assert any(p.tag == "false_long" for p in tr.error_patterns)
    assert tr.feedback  # actionable feedback present
    assert any(a.samples > 0 for a in tr.agents)


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
        assert brief.recommendation == "buy"
        assert len(brief.votes) == 3

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_mtm_evaluates_aged_open(session: AsyncSession):
    tracker = MultiAssetTradeTracker(session)
    trade = await tracker.open_trade(
        desk="crypto",
        symbol="BTC/USD",
        qty=0.01,
        entry_price=100.0,
        brief=DeskBrief(
            desk="crypto",
            symbol="BTC/USD",
            recommendation="buy",
            confidence=0.5,
            score=15,
            summary="mom",
        ),
        is_sim=True,
    )
    row = await session.get(MultiAssetTradeORM, trade.id)
    assert row is not None
    row.opened_at = datetime.now(timezone.utc) - timedelta(hours=30)
    await session.commit()

    with patch(
        "services.multiasset.trade_tracker.quote_symbol",
        AsyncMock(return_value={"symbol": "BTC/USD", "current_price": 110.0}),
    ):
        out = await tracker.evaluate_open_mtm(min_age_hours=24)
    assert out["evaluated"] == 1
    assert out["correct"] == 1
    refreshed = await tracker.get_open("crypto", "BTC/USD")
    assert refreshed is not None
    assert refreshed.was_correct is True
    assert refreshed.status == "open"  # MTM does not close


def test_client_forbidden_includes_beta_api():
    from apis.middleware.access_auth import CLIENT_FORBIDDEN_PREFIXES

    assert "/api/v1/beta" in CLIENT_FORBIDDEN_PREFIXES
