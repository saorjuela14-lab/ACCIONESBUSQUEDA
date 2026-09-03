"""Build the CEO monthly desk report from journal + memory + broker."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.repositories.trade_journal_repository import TradeJournalRepository
from database.url import is_sqlite, normalize_database_url
from domain.firm_capital import FIRM_RETURN_BASE_USD, return_pct_from_base
from domain.month_report import MonthReport, OpenPositionRow, SymbolPnlRow
from services.agent_effectiveness_service import AgentEffectivenessService
from services.desk_learning_service import DeskLearningService
from services.trade_close_review_service import classify_operation
from utils.narrative_es import agent_display_name


class MonthReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._journal = TradeJournalRepository(session)

    async def _equity(self) -> float | None:
        try:
            from services.alpaca_order_service import AlpacaOrderService

            broker = AlpacaOrderService()
            if not broker.is_configured():
                return None
            acct = await broker.get_account()
            eq = float(acct.equity or acct.portfolio_value or 0.0)
            return eq if eq > 0 else None
        except Exception:
            return None

    async def _spy_return(self, *, window_days: int) -> float | None:
        """SPY total return over ~window_days (soft-fail)."""
        try:
            from providers.market.factory import get_market_provider

            period = "1mo" if window_days <= 35 else "3mo"
            df = await get_market_provider().get_history("SPY", period=period, interval="1d")
            if df is None or getattr(df, "empty", True) or len(df) < 2:
                return None
            close_col = "Close" if "Close" in df.columns else df.columns[-1]
            first = float(df[close_col].iloc[0])
            last = float(df[close_col].iloc[-1])
            if first <= 0:
                return None
            return round((last / first - 1.0) * 100.0, 2)
        except Exception:
            return None

    async def build(self, *, window_days: int = 30) -> MonthReport:
        closed = await self._journal.list_closed(limit=500, days=window_days)
        open_rows = await self._journal.list_open()

        outcomes = {"win": 0, "loss": 0, "stagnation": 0, "gestion": 0, "unknown": 0}
        true_tp = 0
        true_stop = 0
        by_sym: dict[str, list[float]] = defaultdict(list)
        by_sym_usd: dict[str, float] = defaultdict(float)
        by_sym_n: dict[str, int] = defaultdict(int)

        for t in closed:
            rev = (t.meta or {}).get("member_review") or {}
            outcome = rev.get("outcome")
            tag = str(rev.get("outcome_tag") or "")
            if outcome not in outcomes:
                outcome, tag = classify_operation(t)
                tag = str(tag or "")
            if outcome not in outcomes:
                outcome = "unknown"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome == "win":
                true_tp += 1
            if outcome == "loss" and (
                tag == "stop" or "stop" in tag or "stop" in (t.exit_reason or "").lower()
            ):
                true_stop += 1

            sym = (t.symbol or "?").upper()
            by_sym_n[sym] += 1
            if t.pnl_pct is not None:
                by_sym[sym].append(float(t.pnl_pct))
            if t.pnl_usd is not None:
                by_sym_usd[sym] += float(t.pnl_usd)

        scored = [t for t in closed if t.pnl_pct is not None]
        wins_pnl = [t for t in scored if (t.pnl_pct or 0) > 0]
        journal_wr = (len(wins_pnl) / len(scored) * 100.0) if scored else None
        avg_pnl = (sum(t.pnl_pct or 0 for t in scored) / len(scored)) if scored else None
        total_pnl = sum(t.pnl_usd or 0 for t in closed if t.pnl_usd is not None)
        closed_pnl = round(total_pnl, 2) if closed else None

        n_closed = len(closed)
        stag_pct = (
            round(outcomes["stagnation"] / n_closed * 100.0, 1) if n_closed else None
        )

        ranked = sorted(
            set(by_sym_n) | set(by_sym_usd),
            key=lambda s: abs(by_sym_usd.get(s, 0.0)),
            reverse=True,
        )[:5]
        top_symbols = [
            SymbolPnlRow(
                symbol=sym,
                closes=by_sym_n.get(sym, 0),
                pnl_usd=round(by_sym_usd.get(sym, 0.0), 2),
                avg_pnl_pct=(
                    round(sum(by_sym[sym]) / len(by_sym[sym]), 2) if by_sym.get(sym) else None
                ),
            )
            for sym in ranked
        ]

        open_pos = [
            OpenPositionRow(
                symbol=t.symbol.upper(),
                qty=float(t.qty or 0),
                entry_price=float(t.entry_price or 0),
                pnl_pct=t.pnl_pct,
                opened_at=t.opened_at,
            )
            for t in open_rows
        ]

        eff = await AgentEffectivenessService(self._session, score_threshold=5.0).summary(
            window_days=window_days
        )
        lessons = await DeskLearningService(self._session).snapshot()
        avoids = [str(a.get("ticker") or "").upper() for a in (lessons.get("avoids") or []) if a.get("ticker")]
        lessons_n = (
            len(lessons.get("avoids") or [])
            + len(lessons.get("notes") or [])
            + len(lessons.get("agent_errors") or [])
        )

        best_label = None
        weak_label = None
        if eff.best_agent:
            best_label = next(
                (a.label_es for a in eff.agents if a.agent_name == eff.best_agent),
                agent_display_name(eff.best_agent),
            )
        if eff.weakest_agent:
            weak_label = next(
                (a.label_es for a in eff.agents if a.agent_name == eff.weakest_agent),
                agent_display_name(eff.weakest_agent),
            )

        equity = await self._equity()
        ret = return_pct_from_base(equity, FIRM_RETURN_BASE_USD) if equity is not None else None
        spy = await self._spy_return(window_days=window_days)
        vs_spy = round(ret - spy, 2) if ret is not None and spy is not None else None

        url = normalize_database_url(get_settings().database_url)
        durable = not is_sqlite(url)

        diagnosis: list[str] = []
        if n_closed == 0:
            diagnosis.append("Sin cierres en la ventana — aún no hay muestra operativa.")
        else:
            if true_tp == 0:
                diagnosis.append("0 take-profit 2R en el mes: no hay edge de objetivo real.")
            if outcomes["stagnation"] >= max(1, n_closed // 3):
                diagnosis.append(
                    f"Estancamiento alto ({outcomes['stagnation']}/{n_closed}): "
                    "cierres EOD/~0% no cuentan como victoria."
                )
            if journal_wr is not None and journal_wr >= 80 and true_tp == 0:
                diagnosis.append(
                    "Win rate por P&L verde engaña: casi no hay TP; revisa estancamiento."
                )
            if top_symbols and abs(top_symbols[0].pnl_usd) > 0 and closed_pnl:
                share = abs(top_symbols[0].pnl_usd) / max(abs(closed_pnl), 1e-9) * 100
                if share >= 50:
                    diagnosis.append(
                        f"Concentración: {top_symbols[0].symbol} aporta ~{share:.0f}% del PnL$ cerrado."
                    )
        if not durable:
            diagnosis.append("SQLite efímero: lecciones/briefs se pierden al redeploy — usa Neon.")

        if ret is not None and spy is not None:
            if vs_spy is not None and vs_spy >= 0:
                headline = f"Equity {ret:+.1f}% vs base $20 · SPY {spy:+.1f}% · mesa {vs_spy:+.1f} pp"
            else:
                headline = f"Equity {ret:+.1f}% vs base $20 · SPY {spy:+.1f}% · rezago {vs_spy:.1f} pp"
        elif ret is not None:
            headline = f"Equity {ret:+.1f}% vs base $20 · {n_closed} cierres · TP {true_tp} / stop {true_stop}"
        else:
            headline = f"{n_closed} cierres · TP {true_tp} · stop {true_stop} · estanc. {outcomes['stagnation']}"

        return MonthReport(
            window_days=window_days,
            base_usd=FIRM_RETURN_BASE_USD,
            equity_usd=round(equity, 2) if equity is not None else None,
            equity_return_pct=ret,
            closed_pnl_usd=closed_pnl,
            closed_avg_pnl_pct=round(avg_pnl, 2) if avg_pnl is not None else None,
            trades_closed=n_closed,
            outcomes=outcomes,
            true_tp=true_tp,
            true_stop=true_stop,
            stagnation_pct=stag_pct,
            journal_win_rate_pct=round(journal_wr, 1) if journal_wr is not None else None,
            thesis_hit_rate_pct=eff.desk_hit_rate_pct,
            theses_correct=eff.theses_correct,
            theses_evaluated=eff.theses_evaluated,
            spy_return_pct=spy,
            vs_spy_pct=vs_spy,
            best_agent=eff.best_agent,
            best_agent_label=best_label,
            weakest_agent=eff.weakest_agent,
            weakest_agent_label=weak_label,
            top_symbols=top_symbols,
            open_positions=open_pos,
            open_count=len(open_pos),
            lessons_active=lessons_n,
            avoids=avoids,
            headline=headline,
            diagnosis=diagnosis,
            durable_db=durable,
            meta={
                "as_of": datetime.now(timezone.utc).isoformat(),
                "method": "classify_operation + member_review + firm base $20",
            },
        )
