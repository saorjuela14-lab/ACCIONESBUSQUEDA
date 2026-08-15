"""Multi-asset trade tracking, evaluation, and effectiveness stats."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.multiasset import AGENT_REGISTRY, quote_symbol
from config.settings import get_settings
from database.models import MultiAssetTradeORM, utc_now
from database.url import is_sqlite, normalize_database_url
from domain.multiasset import (
    AgentDeskStat,
    AssetDeskId,
    DeskBrief,
    ErrorPattern,
    MultiAssetTrade,
    MultiAssetTrackRecord,
)
from utils.logging import get_logger

logger = get_logger(__name__)

ERROR_HINTS: dict[str, tuple[str, str]] = {
    "false_long": (
        "Falso alcista",
        "Brief comprador pero el precio bajó. Exige más confirmación macro/técnico antes de comprar.",
    ),
    "false_short": (
        "Falso bajista",
        "Brief vendedor pero el precio subió. Evita vender en rebote sin confirmación de flujo.",
    ),
    "missed_up": (
        "Movimiento alcista perdido",
        "Hold mientras el activo subió fuerte. Relajar umbral de compra cuando momentum + flujo alinean.",
    ),
    "missed_down": (
        "Caída no anticipada",
        "Hold mientras el activo cayó. Endurecer filtro de riesgo / stop hint.",
    ),
    "correct_long": ("Acierto alcista", "Mantener el patrón de entrada que funcionó."),
    "correct_short": ("Acierto bajista", "Mantener disciplina de salida/venta."),
    "correct_hold": ("Hold correcto", "Bien no forzar trade en rango."),
    "weak_edge": (
        "Señal débil",
        "Retorno pequeño vs ruido. Subir umbral de score/confianza o reducir notional.",
    ),
}

_NOISE_PCT = 1.5
_HIT_PCT = {
    "gold": 2.0,
    "forex": 1.5,
    "crypto": 4.0,
}


def _agent_label(name: str) -> str:
    cls = AGENT_REGISTRY.get(name)
    if cls and hasattr(cls, "label_es"):
        return str(getattr(cls, "label_es"))
    return name.replace("_", " ")


def classify_outcome(
    recommendation: str,
    return_pct: float,
    *,
    desk: str = "gold",
) -> tuple[bool, str, str]:
    """Return (was_correct, error_tag, notes)."""
    hit = float(_HIT_PCT.get(desk, 2.0))
    rec = (recommendation or "hold").lower()
    r = float(return_pct)

    if abs(r) < _NOISE_PCT:
        if rec == "hold":
            return True, "correct_hold", f"Rango estrecho ({r:+.2f}%) — hold correcto"
        return False, "weak_edge", f"Señal {rec} con movimiento mínimo ({r:+.2f}%)"

    if rec == "buy":
        ok = r >= hit
        tag = "correct_long" if ok else "false_long"
        return ok, tag, f"Buy → retorno {r:+.2f}% (umbral +{hit}%)"
    if rec == "sell":
        ok = r <= -hit
        tag = "correct_short" if ok else "false_short"
        return ok, tag, f"Sell → retorno {r:+.2f}% (umbral -{hit}%)"

    if r >= hit:
        return False, "missed_up", f"Hold mientras subió {r:+.2f}%"
    if r <= -hit:
        return False, "missed_down", f"Hold mientras bajó {r:+.2f}%"
    return True, "correct_hold", f"Hold en rango ({r:+.2f}%)"


class MultiAssetTradeTracker:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_domain(self, row: MultiAssetTradeORM) -> MultiAssetTrade:
        try:
            scores = json.loads(row.scores_json or "{}")
        except json.JSONDecodeError:
            scores = {}
        try:
            meta = json.loads(row.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        return MultiAssetTrade(
            id=row.id,
            desk=row.desk,  # type: ignore[arg-type]
            symbol=row.symbol,
            status=row.status,  # type: ignore[arg-type]
            qty=float(row.qty or 0),
            entry_price=float(row.entry_price or 0),
            exit_price=row.exit_price,
            stop_hint=row.stop_hint,
            target_hint=row.target_hint,
            pnl_usd=row.pnl_usd,
            pnl_pct=row.pnl_pct,
            r_multiple=row.r_multiple,
            recommendation=row.recommendation or "hold",
            confidence=row.confidence,
            score=row.score,
            brief_summary=row.brief_summary or "",
            scores={k: float(v) for k, v in scores.items()} if isinstance(scores, dict) else {},
            was_correct=row.was_correct,
            error_tag=row.error_tag,
            eval_notes=row.eval_notes or "",
            is_sim=bool(row.is_sim),
            order_id=row.order_id,
            opened_at=row.opened_at,
            closed_at=row.closed_at,
            evaluated_at=row.evaluated_at,
            meta=meta if isinstance(meta, dict) else {},
        )

    async def get_open(self, desk: AssetDeskId, symbol: str) -> MultiAssetTrade | None:
        row = (
            await self._session.execute(
                select(MultiAssetTradeORM).where(
                    MultiAssetTradeORM.desk == desk,
                    MultiAssetTradeORM.symbol == symbol,
                    MultiAssetTradeORM.status == "open",
                )
            )
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def open_trade(
        self,
        *,
        desk: AssetDeskId,
        symbol: str,
        qty: float,
        entry_price: float,
        brief: DeskBrief | None = None,
        is_sim: bool = False,
        order_id: str | None = None,
        meta: dict | None = None,
    ) -> MultiAssetTrade:
        existing = await self.get_open(desk, symbol)
        scores = {v.agent_name: float(v.score) for v in (brief.votes if brief else [])}
        if existing:
            row = await self._session.get(MultiAssetTradeORM, existing.id)
            assert row is not None
            old_qty = float(row.qty or 0)
            old_px = float(row.entry_price or 0)
            new_qty = old_qty + float(qty)
            if new_qty > 0 and entry_price > 0:
                row.entry_price = ((old_px * old_qty) + (float(entry_price) * float(qty))) / new_qty
            row.qty = new_qty
            if brief:
                row.recommendation = brief.recommendation
                row.confidence = brief.confidence
                row.score = brief.score
                row.brief_summary = brief.summary
                row.stop_hint = brief.stop_hint
                row.target_hint = brief.target_hint
                row.scores_json = json.dumps(scores, default=str)
            if order_id:
                row.order_id = order_id
            row.is_sim = bool(is_sim) and bool(row.is_sim)
            await self._session.commit()
            return self._to_domain(row)

        tid = str(uuid4())
        row = MultiAssetTradeORM(
            id=tid,
            desk=desk,
            symbol=symbol,
            status="open",
            qty=float(qty),
            entry_price=float(entry_price),
            stop_hint=brief.stop_hint if brief else None,
            target_hint=brief.target_hint if brief else None,
            recommendation=(brief.recommendation if brief else "hold"),
            confidence=brief.confidence if brief else None,
            score=brief.score if brief else None,
            brief_summary=brief.summary if brief else None,
            scores_json=json.dumps(scores, default=str),
            is_sim=is_sim,
            order_id=order_id,
            opened_at=utc_now(),
            meta_json=json.dumps(meta or {}, default=str),
        )
        self._session.add(row)
        await self._session.commit()
        return self._to_domain(row)

    async def close_trade(
        self,
        *,
        desk: AssetDeskId,
        symbol: str,
        exit_price: float,
        exit_reason: str | None = None,
    ) -> MultiAssetTrade | None:
        open_t = await self.get_open(desk, symbol)
        if not open_t:
            return None
        row = await self._session.get(MultiAssetTradeORM, open_t.id)
        if not row:
            return None
        entry = float(row.entry_price or 0)
        qty = float(row.qty or 0)
        exit_px = float(exit_price)
        pnl_usd = (exit_px - entry) * qty if entry and qty else None
        pnl_pct = ((exit_px - entry) / entry * 100.0) if entry > 0 else None
        r_mult = None
        stop = row.stop_hint
        if stop is not None and entry > float(stop) and pnl_pct is not None:
            risk = (entry - float(stop)) / entry * 100.0
            if risk > 0:
                r_mult = round(pnl_pct / risk, 2)

        row.status = "closed"
        row.exit_price = exit_px
        row.closed_at = utc_now()
        row.pnl_usd = round(pnl_usd, 4) if pnl_usd is not None else None
        row.pnl_pct = round(pnl_pct, 4) if pnl_pct is not None else None
        row.r_multiple = r_mult

        if pnl_pct is not None:
            ok, tag, notes = classify_outcome(row.recommendation or "hold", pnl_pct, desk=desk)
            if exit_reason:
                notes = f"{notes} · {exit_reason}"
            row.was_correct = ok
            row.error_tag = tag
            row.eval_notes = notes
            row.evaluated_at = utc_now()

        await self._session.commit()
        return self._to_domain(row)

    async def evaluate_open_mtm(self, *, min_age_hours: float = 24.0) -> dict:
        """Mark-to-market evaluate open trades older than min_age (does not close)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        rows = (
            await self._session.execute(
                select(MultiAssetTradeORM).where(
                    MultiAssetTradeORM.status == "open",
                    MultiAssetTradeORM.evaluated_at.is_(None),
                    MultiAssetTradeORM.opened_at <= cutoff,
                )
            )
        ).scalars().all()
        out = {"checked": 0, "evaluated": 0, "correct": 0, "incorrect": 0}
        for row in rows:
            out["checked"] += 1
            entry = float(row.entry_price or 0)
            if entry <= 0:
                continue
            try:
                q = await quote_symbol(row.symbol)
                px = q.get("current_price")
                if not px:
                    continue
                ret = (float(px) - entry) / entry * 100.0
                ok, tag, notes = classify_outcome(
                    row.recommendation or "hold", ret, desk=row.desk
                )
                row.was_correct = ok
                row.error_tag = tag
                row.eval_notes = f"MTM ${float(px):.4f}: {notes}"
                row.evaluated_at = utc_now()
                out["evaluated"] += 1
                if ok:
                    out["correct"] += 1
                else:
                    out["incorrect"] += 1
            except Exception as exc:
                logger.warning("multiasset.mtm_failed", symbol=row.symbol, error=str(exc))
        await self._session.commit()
        logger.info("multiasset.mtm.done", **out)
        return out

    async def list_closed(
        self, *, desk: AssetDeskId | None = None, days: int = 90, limit: int = 40
    ) -> list[MultiAssetTrade]:
        q = select(MultiAssetTradeORM).where(MultiAssetTradeORM.status == "closed")
        if desk:
            q = q.where(MultiAssetTradeORM.desk == desk)
        if days > 0:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.where(MultiAssetTradeORM.closed_at >= since)
        q = q.order_by(MultiAssetTradeORM.closed_at.desc()).limit(limit)
        rows = (await self._session.execute(q)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_open(self, *, desk: AssetDeskId | None = None) -> list[MultiAssetTrade]:
        q = select(MultiAssetTradeORM).where(MultiAssetTradeORM.status == "open")
        if desk:
            q = q.where(MultiAssetTradeORM.desk == desk)
        rows = (await self._session.execute(q)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def track_record(
        self, *, desk: AssetDeskId | None = None, window_days: int = 90
    ) -> MultiAssetTrackRecord:
        closed = await self.list_closed(desk=desk, days=window_days, limit=500)
        open_rows = await self.list_open(desk=desk)

        wins = [t for t in closed if (t.pnl_pct or 0) > 0]
        losses = [t for t in closed if t.pnl_pct is not None and (t.pnl_pct or 0) <= 0]
        scored = [t for t in closed if t.pnl_pct is not None]
        win_rate = (len(wins) / len(scored) * 100.0) if scored else None
        avg_pnl = (sum(t.pnl_pct or 0 for t in scored) / len(scored)) if scored else None
        total_pnl = sum(t.pnl_usd or 0 for t in closed if t.pnl_usd is not None) or None

        evaluated_q = select(MultiAssetTradeORM).where(MultiAssetTradeORM.was_correct.is_not(None))
        if desk:
            evaluated_q = evaluated_q.where(MultiAssetTradeORM.desk == desk)
        eval_rows = (await self._session.execute(evaluated_q)).scalars().all()
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        def _aware(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        eval_rows = [
            r
            for r in eval_rows
            if _aware(r.evaluated_at) is None or (_aware(r.evaluated_at) or since) >= since
        ]
        briefs_correct = sum(1 for r in eval_rows if r.was_correct is True)
        brief_hit = (briefs_correct / len(eval_rows) * 100.0) if eval_rows else None

        pending_q = select(MultiAssetTradeORM).where(
            MultiAssetTradeORM.evaluated_at.is_(None),
            MultiAssetTradeORM.status == "open",
        )
        if desk:
            pending_q = pending_q.where(MultiAssetTradeORM.desk == desk)
        pending = (await self._session.execute(pending_q)).scalars().all()

        tag_counts: dict[str, int] = defaultdict(int)
        for r in eval_rows:
            if r.error_tag and r.was_correct is False:
                tag_counts[r.error_tag] += 1
        err_total = sum(tag_counts.values()) or 0
        patterns: list[ErrorPattern] = []
        for tag, n in sorted(tag_counts.items(), key=lambda x: -x[1]):
            label, hint = ERROR_HINTS.get(tag, (tag, "Revisar reglas de la mesa."))
            patterns.append(
                ErrorPattern(
                    tag=tag,
                    label_es=label,
                    count=n,
                    share_pct=round(n / err_total * 100.0, 1) if err_total else None,
                    hint_es=hint,
                )
            )

        hits: dict[str, int] = defaultdict(int)
        misses: dict[str, int] = defaultdict(int)
        score_right: dict[str, list[float]] = defaultdict(list)
        score_wrong: dict[str, list[float]] = defaultdict(list)
        threshold = 5.0
        for r in eval_rows:
            try:
                scores = json.loads(r.scores_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(scores, dict) or r.was_correct is None:
                continue
            for agent, raw in scores.items():
                try:
                    sc = float(raw)
                except (TypeError, ValueError):
                    continue
                if abs(sc) < threshold:
                    continue
                agent_bull = sc > 0
                if r.pnl_pct is not None:
                    actual_up = float(r.pnl_pct) > 0
                    right = agent_bull == actual_up
                else:
                    brief_bull = (r.recommendation or "").lower() == "buy"
                    brief_bear = (r.recommendation or "").lower() == "sell"
                    if not (brief_bull or brief_bear):
                        continue
                    right = (sc > 0 and r.was_correct and brief_bull) or (
                        sc < 0 and r.was_correct and brief_bear
                    ) or (sc > 0 and not r.was_correct and brief_bear) or (
                        sc < 0 and not r.was_correct and brief_bull
                    )
                if right:
                    hits[agent] += 1
                    score_right[agent].append(sc)
                else:
                    misses[agent] += 1
                    score_wrong[agent].append(sc)

        from services.multiasset.desks import DESKS

        known: set[str] = set()
        if desk:
            known.update(DESKS[desk].agent_names)
        else:
            for d in DESKS.values():
                known.update(d.agent_names)
        known |= set(hits) | set(misses)

        agents: list[AgentDeskStat] = []
        for name in sorted(known):
            n = hits[name] + misses[name]
            hr = (hits[name] / n * 100.0) if n else None
            ar = (
                sum(score_right[name]) / len(score_right[name]) if score_right[name] else None
            )
            aw = (
                sum(score_wrong[name]) / len(score_wrong[name]) if score_wrong[name] else None
            )
            agents.append(
                AgentDeskStat(
                    agent_name=name,
                    label_es=_agent_label(name),
                    samples=n,
                    hits=hits[name],
                    misses=misses[name],
                    hit_rate_pct=round(hr, 1) if hr is not None else None,
                    avg_score_when_right=round(ar, 1) if ar is not None else None,
                    avg_score_when_wrong=round(aw, 1) if aw is not None else None,
                )
            )
        agents.sort(key=lambda a: (0 if a.samples else 1, -(a.hit_rate_pct or -1)))

        ranked = [a for a in agents if a.samples >= 2 and a.hit_rate_pct is not None]
        best = max(ranked, key=lambda a: a.hit_rate_pct or 0).agent_name if ranked else None
        weak = min(ranked, key=lambda a: a.hit_rate_pct or 0).agent_name if ranked else None

        feedback: list[str] = []
        if patterns:
            top = patterns[0]
            feedback.append(f"Error dominante: {top.label_es} ({top.count}). {top.hint_es}")
        if weak and best and weak != best:
            feedback.append(
                f"Reforzar {_agent_label(weak)}; el más fiable ahora es {_agent_label(best)}."
            )
        if win_rate is not None and win_rate < 45 and len(scored) >= 3:
            feedback.append(
                "Win rate <45% con N≥3 — reducir notional y exigir score de brief más alto antes de comprar."
            )
        if not feedback and len(scored) == 0:
            feedback.append(
                "Aún no hay trades cerrados. Compra (o dry-run) y vende para empezar el ciclo de aprendizaje."
            )

        url = normalize_database_url(get_settings().database_url)
        return MultiAssetTrackRecord(
            desk=desk,
            window_days=window_days,
            trades_closed=len(closed),
            trades_wins=len(wins),
            trades_losses=len(losses),
            trades_win_rate_pct=round(win_rate, 1) if win_rate is not None else None,
            trades_avg_pnl_pct=round(avg_pnl, 2) if avg_pnl is not None else None,
            trades_total_pnl_usd=round(total_pnl, 2) if total_pnl is not None else None,
            briefs_evaluated=len(eval_rows),
            briefs_correct=briefs_correct,
            brief_hit_rate_pct=round(brief_hit, 1) if brief_hit is not None else None,
            open_trades=len(open_rows),
            pending_eval=len(pending),
            recent_closed=closed[:12],
            error_patterns=patterns,
            agents=agents,
            best_agent=best,
            weakest_agent=weak,
            feedback=feedback,
            durable_db=not is_sqlite(url),
        )
