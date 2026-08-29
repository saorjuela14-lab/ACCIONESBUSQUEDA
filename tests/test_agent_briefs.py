"""Persist per-agent justification, critique on close, clip repeating errors."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, InvestmentMemoryORM
from database.repositories.desk_lesson_repository import DeskLessonRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from domain.agent_briefs import (
    apply_agent_error_lessons,
    compact_agent_briefs,
    critique_agent,
    infer_pattern,
    original_score,
)
from domain.entities import InvestmentMemoryRecord
from domain.enums import EvidenceCategory
from domain.reports import AgentReport, Finding
from services.desk_learning_service import DeskLearningService
from services.trade_journal_service import TradeJournalService


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _report(
    name: str,
    score: float,
    summary: str,
    finding: str,
    *,
    risk: str | None = None,
) -> AgentReport:
    findings = [
        Finding(category=EvidenceCategory.INTERPRETATION, statement=finding, confidence=0.7)
    ]
    risks = []
    if risk:
        risks.append(Finding(category=EvidenceCategory.RISK, statement=risk, confidence=0.6))
    return AgentReport(
        agent_name=name,
        score=score,
        confidence=0.7,
        findings=findings,
        risks=risks,
        summary=summary,
    )


def test_compact_and_infer_pattern():
    reports = [
        _report(
            "technical_agent",
            22,
            "Ruptura de máximos intradía",
            "Breakout sobre resistencia",
            risk="Falta volumen",
        ),
        _report("news_agent", -12, "Sin catalizador", "Nada nuevo"),
    ]
    briefs = compact_agent_briefs(reports)
    assert briefs["technical_agent"]["stance"] == "long"
    assert "Ruptura" in briefs["technical_agent"]["summary"]
    assert briefs["technical_agent"]["findings"]
    assert infer_pattern(
        briefs["technical_agent"]["summary"],
        briefs["technical_agent"]["findings"],
    ) == "breakout_failed"


def test_critique_wrong_long_is_detailed():
    brief = {
        "score": 20,
        "summary": "Sobreventa RSI y rebote técnico",
        "findings": ["RSI oversold en 15m"],
        "risks": ["Mercado débil"],
    }
    out = critique_agent(
        agent_name="technical_agent",
        brief=brief,
        outcome="loss",
        outcome_tag="stop",
        ticker="AMC",
        pnl_pct=-8.0,
    )
    assert out["verdict"] == "wrong"
    assert out["pattern"] == "oversold_failed"
    assert "AMC" in out["why"]
    assert "oversold" in out["why"].lower() or "Sobreventa" in out["why"] or "RSI" in out["why"]
    assert "Ajuste" in out["why"]


def test_critique_false_veto_on_win():
    out = critique_agent(
        agent_name="news_agent",
        brief={"score": -15, "summary": "Sin catalizador, evitar", "findings": ["Nada en news"]},
        outcome="win",
        outcome_tag="take_profit",
        ticker="AMC",
        pnl_pct=16.0,
    )
    assert out["verdict"] == "wrong"
    assert out["pattern"] == "false_veto"
    assert "vetó" in out["why"].lower() or "veto" in out["why"].lower()


def test_critique_stagnation_is_opportunity_cost():
    out = critique_agent(
        agent_name="technical_agent",
        brief={"score": 20, "summary": "Momentum intradía", "findings": ["Ruptura menor"]},
        outcome="stagnation",
        outcome_tag="no_progress",
        ticker="AMC",
        pnl_pct=0.2,
    )
    assert out["verdict"] == "wrong"
    assert out["pattern"] == "stagnation_failed"
    assert "estanc" in out["why"].lower()
    warned = critique_agent(
        agent_name="news_agent",
        brief={"score": -12, "summary": "Sin catalizador", "findings": []},
        outcome="stagnation",
        outcome_tag="no_progress",
        ticker="AMC",
        pnl_pct=0.2,
    )
    assert warned["verdict"] == "correct"


def test_apply_lessons_clips_repeating_breakout():
    report = _report(
        "technical_agent",
        22,
        "Nueva ruptura de máximos",
        "Breakout confirmado",
    )
    errors = {
        "technical_agent": [
            {
                "ticker": "AMC",
                "pattern": "breakout_failed",
                "reason": "Dijo ruptura y el stop se tocó en AMC.",
                "findings": ["Breakout sobre resistencia"],
            }
        ]
    }
    apply_agent_error_lessons([report], errors)
    assert original_score(report) == 22
    assert report.score == pytest.approx(10.0)
    assert any("LECCIÓN" in f.statement for f in report.findings)
    assert "memoria de error" in (report.summary or "")
    persisted = compact_agent_briefs([report])["technical_agent"]
    assert "memoria de error" not in persisted["summary"]
    assert "ruptura" in persisted["summary"].lower()


def test_apply_lessons_does_not_clip_unrelated_setup():
    report = _report(
        "technical_agent",
        18,
        "Tendencia de fondo estable",
        "Precio sobre media 20",
    )
    errors = {
        "technical_agent": [
            {
                "ticker": "ZZZ",
                "pattern": "catalyst_failed",
                "reason": "Compró por earnings y falló.",
                "findings": ["earnings beat"],
            }
        ]
    }
    apply_agent_error_lessons([report], errors)
    assert report.score == pytest.approx(18.0)
    assert any("LECCIÓN" in f.statement for f in report.findings)


@pytest.mark.asyncio
async def test_briefs_persist_and_close_writes_agent_error(session: AsyncSession):
    briefs = compact_agent_briefs(
        [
            _report(
                "technical_agent",
                20,
                "Ruptura de máximos",
                "Breakout sobre 2.80",
            )
        ]
    )
    await InvestmentMemoryRepository(session).save(
        InvestmentMemoryRecord(
            ticker="AMC",
            thesis="buy amc",
            scores={"technical_agent": 20, "news_agent": -12},
            briefs=briefs,
            confidence=0.6,
            scenario="base",
            expected_outcome="up",
            recommendation="buy",
            entry_price=2.70,
        )
    )
    loaded = (await InvestmentMemoryRepository(session).latest_by_ticker(["AMC"]))["AMC"]
    assert loaded.briefs["technical_agent"]["summary"].startswith("Ruptura")

    svc = TradeJournalService(session)
    await svc.record_open(
        symbol="AMC", qty=2, entry_price=2.70, stop_loss=2.48, take_profit=3.13
    )
    await svc.record_close(
        symbol="AMC",
        exit_price=2.48,
        exit_reason="Stop/trailing tocado @ 2.48 ≤ 2.48",
    )
    closed = (await svc.list_closed(days=1))[0]
    review = closed.meta["member_review"]
    by_name = {m["agent"]: m for m in review["members"]}
    tech = by_name["technical_agent"]
    assert tech["right"] is False
    assert tech["pattern"] == "breakout_failed"
    assert "Ruptura" in (tech["why"] or "") or "breakout" in (tech["why"] or "").lower()
    assert review["lessons"]

    errors = await DeskLearningService(session).active_errors_by_agent()
    assert "technical_agent" in errors
    assert errors["technical_agent"][0]["pattern"] == "breakout_failed"

    rows = await DeskLessonRepository(session).list_active(lesson_type="agent_error")
    assert len(rows) >= 1
    assert rows[0].agent_name == "technical_agent"


@pytest.mark.asyncio
async def test_legacy_row_without_briefs_json_loads(session: AsyncSession):
    now = datetime.now(timezone.utc)
    session.add(
        InvestmentMemoryORM(
            id=str(uuid4()),
            ticker="PLUG",
            thesis="t",
            reasons_json="[]",
            scores_json='{"technical_agent": 18}',
            briefs_json="{}",
            confidence=0.5,
            scenario="base",
            expected_outcome="up",
            recommendation="buy",
            created_at=now,
        )
    )
    await session.commit()
    rec = (await InvestmentMemoryRepository(session).latest_by_ticker(["PLUG"]))["PLUG"]
    assert rec.briefs == {}
    out = critique_agent(
        agent_name="technical_agent",
        brief={"score": 18},
        outcome="loss",
        outcome_tag="stop",
        ticker="PLUG",
        pnl_pct=-8,
    )
    assert out["verdict"] == "wrong"
    assert "sin justificación guardada" in out["why"]
