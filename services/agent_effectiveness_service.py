"""Compute per-agent decision effectiveness from evaluated investment memory."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.investment_director import InvestmentDirector
from config.settings import get_settings
from database.models import AgentWeightORM, InvestmentMemoryORM
from database.url import is_sqlite, normalize_database_url
from domain.agent_effectiveness import AgentEffectivenessRow, DeskEffectivenessSummary
from utils.narrative_es import agent_display_name


class AgentEffectivenessService:
    """Score each committee agent vs subsequent returns / thesis outcomes."""

    def __init__(self, session: AsyncSession, *, score_threshold: float = 5.0) -> None:
        self._session = session
        self._threshold = float(score_threshold)

    async def summary(self, *, window_days: int = 1) -> DeskEffectivenessSummary:
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        def _aware(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        rows = (
            await self._session.execute(
                select(InvestmentMemoryORM).where(InvestmentMemoryORM.was_correct.is_not(None))
            )
        ).scalars().all()

        evaluated = [
            r
            for r in rows
            if _aware(r.evaluated_at) is None or _aware(r.evaluated_at) >= since
        ]

        pending_n = (
            await self._session.execute(
                select(InvestmentMemoryORM).where(InvestmentMemoryORM.evaluated_at.is_(None))
            )
        ).scalars().all()
        # Count pending that are old enough to matter (created in window or earlier)
        pending_count = len(pending_n)

        weights_rows = (await self._session.execute(select(AgentWeightORM))).scalars().all()
        weights = {r.agent_name: float(r.weight) for r in weights_rows}
        stored_acc = {r.agent_name: float(r.accuracy) for r in weights_rows}
        defaults = dict(InvestmentDirector.DEFAULT_WEIGHTS)

        # Per-agent accumulators
        hits: dict[str, int] = defaultdict(int)
        misses: dict[str, int] = defaultdict(int)
        score_right: dict[str, list[float]] = defaultdict(list)
        score_wrong: dict[str, list[float]] = defaultdict(list)
        align_hit: dict[str, int] = defaultdict(int)
        align_n: dict[str, int] = defaultdict(int)
        seen_agents: set[str] = set(defaults.keys())

        theses_correct = 0
        for rec in evaluated:
            if rec.was_correct:
                theses_correct += 1
            try:
                import json

                scores = json.loads(rec.scores_json or "{}")
            except Exception:
                scores = {}
            if not isinstance(scores, dict):
                continue
            ret = rec.actual_return_pct
            for agent, raw in scores.items():
                if agent in ("investment_director",):
                    continue
                try:
                    score = float(raw)
                except (TypeError, ValueError):
                    continue
                seen_agents.add(agent)
                # Committee alignment (same logic as weight recalibration)
                if abs(score) >= self._threshold and rec.was_correct is not None:
                    align_n[agent] += 1
                    agent_aligned = (score > 0 and rec.was_correct) or (
                        score < 0 and not rec.was_correct
                    )
                    if agent_aligned:
                        align_hit[agent] += 1

                # Directional vs realized return
                if ret is None or abs(score) < self._threshold:
                    continue
                predicted_up = score > 0
                actual_up = float(ret) > 0
                # For sells that were "correct" with large negative return, direction is down
                if predicted_up == actual_up:
                    hits[agent] += 1
                    score_right[agent].append(score)
                else:
                    misses[agent] += 1
                    score_wrong[agent].append(score)

        agent_rows: list[AgentEffectivenessRow] = []
        for agent in sorted(seen_agents):
            n = hits[agent] + misses[agent]
            hit_rate = (hits[agent] / n * 100.0) if n else None
            align_rate = (
                (align_hit[agent] / align_n[agent] * 100.0) if align_n[agent] else None
            )
            avg_r = (
                sum(score_right[agent]) / len(score_right[agent])
                if score_right[agent]
                else None
            )
            avg_w = (
                sum(score_wrong[agent]) / len(score_wrong[agent])
                if score_wrong[agent]
                else None
            )
            agent_rows.append(
                AgentEffectivenessRow(
                    agent_name=agent,
                    label_es=agent_display_name(agent).title(),
                    samples=n,
                    hits=hits[agent],
                    misses=misses[agent],
                    hit_rate_pct=round(hit_rate, 1) if hit_rate is not None else None,
                    avg_score_when_right=round(avg_r, 1) if avg_r is not None else None,
                    avg_score_when_wrong=round(avg_w, 1) if avg_w is not None else None,
                    committee_align_pct=round(align_rate, 1) if align_rate is not None else None,
                    current_weight=round(
                        float(weights.get(agent, defaults.get(agent, 1.0))), 2
                    ),
                    stored_accuracy=(
                        round(stored_acc[agent], 2) if agent in stored_acc else None
                    ),
                )
            )

        # Rank agents with enough samples
        ranked = [a for a in agent_rows if a.samples >= 3 and a.hit_rate_pct is not None]
        best = max(ranked, key=lambda a: a.hit_rate_pct or 0).agent_name if ranked else None
        weak = min(ranked, key=lambda a: a.hit_rate_pct or 0).agent_name if ranked else None

        theses_n = len(evaluated)
        desk_rate = (theses_correct / theses_n * 100.0) if theses_n else None
        url = normalize_database_url(get_settings().database_url)

        # Sort: those with samples first by hit rate desc, then by name
        agent_rows.sort(
            key=lambda a: (
                0 if a.samples else 1,
                -(a.hit_rate_pct or -1),
                a.label_es.lower(),
            )
        )

        return DeskEffectivenessSummary(
            window_days=window_days,
            score_threshold=self._threshold,
            theses_evaluated=theses_n,
            theses_correct=theses_correct,
            desk_hit_rate_pct=round(desk_rate, 1) if desk_rate is not None else None,
            theses_pending=pending_count,
            agents=agent_rows,
            best_agent=best,
            weakest_agent=weak,
            durable_db=not is_sqlite(url),
            meta={
                "min_samples_for_rank": 3,
                "agents_with_calls": sum(1 for a in agent_rows if a.samples > 0),
            },
        )
