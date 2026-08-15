"""Multi-asset beta autopilot — capital-aware buys/sells per desk."""

from __future__ import annotations

from typing import Any

from config.settings import get_settings
from domain.multiasset import AssetDeskId, MultiAssetOrderRequest
from services.kill_switch_service import KillSwitchService
from services.multiasset.desk_service import MultiAssetDeskService
from services.multiasset.desks import DESKS, get_desk
from services.multiasset.paper_broker import get_beta_broker_provider
from services.multiasset.trade_tracker import MultiAssetTradeTracker
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger
from utils.market_hours import is_market_open

logger = get_logger(__name__)

# Share of the multi-asset sleeve when US equity session is open
_DESK_WEIGHTS_RTH: dict[AssetDeskId, float] = {
    "gold": 0.40,
    "forex": 0.25,
    "crypto": 0.35,
}
# Off-hours / weekend: park full deployable sleeve in crypto (24/7)
_DESK_WEIGHTS_OFFHOURS: dict[AssetDeskId, float] = {
    "gold": 0.0,
    "forex": 0.0,
    "crypto": 1.0,
}


class MultiAssetAutopilotService:
    """One cycle: size by capital → manage open risk → brief → buy/sell paper/sim."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()
        self._desk = MultiAssetDeskService(session)
        self._tracker = MultiAssetTradeTracker(session)
        self._broker = get_beta_broker_provider()

    def _weights(self, market_open: bool) -> dict[AssetDeskId, float]:
        if market_open:
            return dict(_DESK_WEIGHTS_RTH)
        if getattr(self._settings, "multiasset_offhours_crypto_full_capital", True):
            return dict(_DESK_WEIGHTS_OFFHOURS)
        return dict(_DESK_WEIGHTS_RTH)

    async def run(self, *, actor: str = "multiasset_autopilot") -> dict[str, Any]:
        out: dict[str, Any] = {"actor": actor, "desks": {}, "skipped": None}
        if not self._settings.multiasset_beta_enabled:
            out["skipped"] = "multiasset_beta_disabled"
            return out
        if not getattr(self._settings, "multiasset_autopilot_enabled", True):
            out["skipped"] = "multiasset_autopilot_disabled"
            return out

        if await KillSwitchService(self._session).is_active():
            out["skipped"] = "kill_switch_active"
            return out

        dry = bool(getattr(self._settings, "multiasset_autopilot_dry_run", False))
        if not dry and not self._broker.is_configured():
            dry = True
            out["forced_dry_run"] = "broker_unconfigured"

        market_open = is_market_open()
        out["market_open"] = market_open
        weights = self._weights(market_open)
        out["allocation_mode"] = "rth_split" if market_open else "offhours_crypto_100"

        # Expand crypto universe beyond BTC/ETH/SOL (Alpaca USD pairs)
        try:
            n = await self._desk.sync_crypto_universe()
            out["crypto_universe"] = n
        except Exception as exc:
            out["crypto_universe_error"] = str(exc)

        capital = await self._capital_snapshot(offhours_crypto=not market_open)
        out["capital"] = capital
        sleeve = float(capital["sleeve_usd"])
        cash = float(capital["cash_usd"])
        reserve = float(capital["reserve_usd"])
        deployable = max(0.0, min(sleeve, cash - reserve))
        out["deployable_usd"] = round(deployable, 2)
        out["weights"] = weights

        for desk_id, weight in weights.items():
            if weight <= 0 and desk_id != "crypto":
                out["desks"][desk_id] = {
                    "skipped": "market_closed_capital_to_crypto",
                    "budget": 0,
                    "weight": 0,
                }
                continue
            desk_budget = deployable * weight
            try:
                out["desks"][desk_id] = await self._run_desk(
                    desk_id,
                    desk_budget=desk_budget,
                    dry_run=dry,
                    market_open=market_open,
                    actor=actor,
                )
                out["desks"][desk_id]["weight"] = weight
            except Exception as exc:
                logger.warning("multiasset.autopilot.desk_failed", desk=desk_id, error=str(exc))
                out["desks"][desk_id] = {"error": str(exc)}

        logger.info(
            "multiasset.autopilot.done",
            dry_run=dry,
            mode=out["allocation_mode"],
            deployable=out["deployable_usd"],
            buys=sum(len(d.get("buys") or []) for d in out["desks"].values() if isinstance(d, dict)),
            sells=sum(len(d.get("sells") or []) for d in out["desks"].values() if isinstance(d, dict)),
        )
        return out

    async def _capital_snapshot(self, *, offhours_crypto: bool = False) -> dict[str, float]:
        """Equity/cash from paper beta account; fall back to configured notional caps."""
        equity = float(getattr(self._settings, "multiasset_fallback_equity", 10_000) or 10_000)
        cash = equity
        if self._broker.is_configured():
            try:
                acct = await self._broker.get_account()
                equity = float(acct.get("equity") or equity)
                cash = float(acct.get("cash") or cash)
            except Exception as exc:
                logger.warning("multiasset.autopilot.account_failed", error=str(exc))

        # Simulation: off-hours → nearly full paper capital to crypto sleeve
        if offhours_crypto and getattr(
            self._settings, "multiasset_offhours_crypto_full_capital", True
        ):
            sleeve_pct = float(
                getattr(self._settings, "multiasset_offhours_sleeve_pct", 100.0) or 100.0
            ) / 100.0
            reserve_pct = float(
                getattr(self._settings, "multiasset_offhours_cash_reserve_pct", 5.0) or 5.0
            ) / 100.0
            sleeve = equity * sleeve_pct
        else:
            sleeve_pct = float(getattr(self._settings, "multiasset_sleeve_pct", 30.0) or 30.0) / 100.0
            reserve_pct = float(getattr(self._settings, "multiasset_cash_reserve_pct", 15.0) or 15.0) / 100.0
            max_total = float(self._settings.multiasset_beta_max_notional or 500)
            sleeve_cap = float(getattr(self._settings, "multiasset_sleeve_cap_usd", 5_000) or 5_000)
            sleeve = min(equity * sleeve_pct, max(sleeve_cap, max_total * 3))
        reserve = equity * reserve_pct
        return {
            "equity_usd": round(equity, 2),
            "cash_usd": round(cash, 2),
            "sleeve_usd": round(sleeve, 2),
            "reserve_usd": round(reserve, 2),
            "sleeve_pct": sleeve_pct * 100,
            "reserve_pct": reserve_pct * 100,
        }

    def _size_notional(
        self,
        *,
        desk: AssetDeskId,
        desk_budget: float,
        open_notional: float,
        score: float,
        confidence: float,
        max_open: int = 3,
    ) -> float:
        strategy = get_desk(desk)
        room = max(0.0, desk_budget - open_notional)
        if desk == "crypto":
            # Simulation: allow larger clips — ~12% of budget per name, uncapped opens
            per_slot = max(
                50.0,
                min(
                    float(getattr(self._settings, "multiasset_crypto_max_notional", 25_000) or 25_000),
                    desk_budget * 0.12,
                ),
            )
            strat_cap = float(
                getattr(self._settings, "multiasset_crypto_max_notional", 0) or 0
            ) or max(float(strategy.max_notional_usd), per_slot)
            cap = min(strat_cap, per_slot, room)
        else:
            cap = min(
                float(strategy.max_notional_usd),
                float(self._settings.multiasset_beta_max_notional or 500),
                room,
            )
        if cap < 15:
            return 0.0
        conf = max(0.30, min(1.0, confidence))
        score_f = min(1.0, abs(score) / 25.0)  # micro-like: reach full size sooner
        frac = 0.45 + 0.55 * (0.5 * conf + 0.5 * score_f)
        return round(max(15.0, min(cap, cap * frac)), 2)

    async def _run_desk(
        self,
        desk: AssetDeskId,
        *,
        desk_budget: float,
        dry_run: bool,
        market_open: bool,
        actor: str,
    ) -> dict[str, Any]:
        strategy = get_desk(desk)
        # ETFs need RTH unless simulating; crypto is 24/7
        if desk != "crypto" and not market_open and not dry_run:
            return {"skipped": "market_closed", "budget": desk_budget}
        if desk != "crypto" and not market_open and dry_run:
            # Still allow sim overnight for learning on weekends
            pass

        open_trades = await self._tracker.list_open(desk=desk)
        open_by_sym = {t.symbol: t for t in open_trades}
        open_notional = sum(
            float(t.entry_price or 0) * float(t.qty or 0) for t in open_trades
        )
        # 0 / negative => unlimited open positions (strategy simulation)
        raw_max = int(getattr(self._settings, "multiasset_max_open_per_desk", 0) or 0)
        max_open = 10_000 if raw_max <= 0 else raw_max
        # Crypto sim: micro-like gates
        if desk == "crypto":
            min_score = float(getattr(self._settings, "multiasset_crypto_min_score_buy", 3) or 3)
            min_conf = float(getattr(self._settings, "multiasset_crypto_min_confidence", 0.30) or 0.30)
        else:
            min_score = float(getattr(self._settings, "multiasset_min_score_buy", 12) or 12)
            min_conf = float(getattr(self._settings, "multiasset_min_confidence", 0.45) or 0.45)

        buys: list[dict] = []
        sells: list[dict] = []
        holds: list[str] = []
        scanned: list[dict] = []

        # 1) Manage open risk / exits first (cash flow + risk)
        for sym, trade in list(open_by_sym.items()):
            try:
                brief = await self._desk.brief(desk, sym)
            except Exception as exc:
                sells.append({"symbol": sym, "skipped": f"brief_failed: {exc}"})
                continue
            exit_reason = None
            px = brief.entry_hint
            if px and trade.entry_price > 0:
                ret = (float(px) - float(trade.entry_price)) / float(trade.entry_price)
                if trade.stop_hint and float(px) <= float(trade.stop_hint):
                    exit_reason = "stop_hint"
                elif trade.target_hint and float(px) >= float(trade.target_hint):
                    exit_reason = "target_hint"
                elif brief.recommendation == "sell" and brief.score <= -min_score:
                    exit_reason = "brief_sell"
                elif brief.recommendation == "hold" and ret <= -float(strategy.default_stop_pct):
                    exit_reason = "soft_stop"
            elif brief.recommendation == "sell" and brief.score <= -min_score:
                exit_reason = "brief_sell"

            if exit_reason:
                qty = float(trade.qty or 0) or None
                notional = None
                if desk == "crypto" and (qty is None or qty <= 0):
                    notional = 25.0
                req = MultiAssetOrderRequest(
                    desk=desk,
                    symbol=sym,
                    side="sell",
                    qty=qty,
                    notional=notional,
                    dry_run=dry_run,
                    confirm=not dry_run,
                    note=f"autopilot:{exit_reason}:{actor}",
                )
                try:
                    res = await self._desk.execute(req)
                    sells.append(
                        {
                            "symbol": sym,
                            "reason": exit_reason,
                            "ok": res.ok,
                            "message": res.message,
                            "pnl_pct": (res.payload or {}).get("pnl_pct"),
                            "error_tag": (res.payload or {}).get("error_tag"),
                        }
                    )
                    open_by_sym.pop(sym, None)
                    open_notional = max(
                        0.0,
                        open_notional - float(trade.entry_price or 0) * float(trade.qty or 0),
                    )
                except Exception as exc:
                    sells.append({"symbol": sym, "error": str(exc), "reason": exit_reason})
            else:
                holds.append(sym)

        # 2) Rank fresh entries — crypto: chart pre-screen entire universe, then full brief
        candidates: list[tuple[float, str, Any]] = []
        symbols_to_brief = list(strategy.symbols)

        if desk == "crypto":
            from agents.multiasset import CryptoChartTechnicalAgent
            import asyncio

            chart_agent = CryptoChartTechnicalAgent()
            pre: list[tuple[float, str]] = []

            async def _screen(sym: str):
                try:
                    rep = await chart_agent.analyze(sym)
                    return sym, float(rep.score), rep.summary
                except Exception as exc:
                    return sym, -999.0, str(exc)

            # Bound concurrency
            sem = asyncio.Semaphore(6)

            async def _bounded(sym: str):
                async with sem:
                    return await _screen(sym)

            results = await asyncio.gather(
                *[_bounded(i.symbol) for i in strategy.symbols if i.symbol not in open_by_sym]
            )
            chart_min = float(getattr(self._settings, "multiasset_crypto_chart_prescreen", 6) or 6)
            for sym, sc, summary in results:
                if sc <= -900:
                    scanned.append({"symbol": sym, "skip": f"chart_failed: {summary}"})
                    continue
                if sc < chart_min:
                    scanned.append(
                        {
                            "symbol": sym,
                            "rec": "hold",
                            "score": round(sc, 1),
                            "skip": f"prescreen_chart<{chart_min}",
                        }
                    )
                    continue
                pre.append((sc, sym))
            pre.sort(key=lambda x: -x[0])
            # Full committee on all chart-qualified names (unlimited opens)
            symbols_to_brief = [
                next(i for i in strategy.symbols if i.symbol == sym) for _, sym in pre
            ]
            universe_n = len(strategy.symbols)
            prequalified = len(pre)
        else:
            universe_n = len(strategy.symbols)
            prequalified = len(symbols_to_brief)

        for item in symbols_to_brief:
            if item.symbol in open_by_sym:
                continue
            try:
                brief = await self._desk.brief(desk, item.symbol)
            except Exception as exc:
                scanned.append({"symbol": item.symbol, "skip": f"brief_failed: {exc}"})
                continue
            row = {
                "symbol": item.symbol,
                "rec": brief.recommendation,
                "score": round(brief.score, 1),
                "confidence": round(brief.confidence or 0, 2),
            }
            if brief.recommendation != "buy":
                row["skip"] = f"rec={brief.recommendation}"
                scanned.append(row)
                continue
            if brief.score < min_score or (brief.confidence or 0) < min_conf:
                row["skip"] = f"below_gate score>={min_score} conf>={min_conf}"
                scanned.append(row)
                continue
            if desk == "crypto":
                chart_vote = next(
                    (v for v in brief.votes if v.agent_name == "crypto_chart_technical_agent"),
                    None,
                )
                if chart_vote is None or chart_vote.score < 6:
                    row["skip"] = (
                        f"chart_gate score={chart_vote.score if chart_vote else 'n/a'} (need≥6)"
                    )
                    scanned.append(row)
                    continue
            if desk != "crypto":
                riskish = [
                    v
                    for v in brief.votes
                    if "risk" in v.agent_name and v.score <= -20
                ]
                if riskish and brief.score < min_score + 8:
                    row["skip"] = "risk_veto"
                    scanned.append(row)
                    continue
            scanned.append({**row, "skip": None})
            candidates.append((brief.score * (brief.confidence or 0.5), item.symbol, brief))

        candidates.sort(key=lambda x: -x[0])
        slots = max(0, max_open - len(open_by_sym))

        for _, sym, brief in candidates[:slots]:
            notional = self._size_notional(
                desk=desk,
                desk_budget=desk_budget,
                open_notional=open_notional,
                score=brief.score,
                confidence=brief.confidence or 0.5,
                max_open=max_open,
            )
            if notional <= 0:
                scanned.append({"symbol": sym, "skip": "no_budget_room"})
                break
            qty = None
            if desk != "crypto" and brief.entry_hint:
                qty = round(notional / float(brief.entry_hint), 4)
                if qty < 0.01:
                    continue
                notional_arg = None
            else:
                notional_arg = notional
                qty = None

            req = MultiAssetOrderRequest(
                desk=desk,
                symbol=sym,
                side="buy",
                qty=qty,
                notional=notional_arg,
                dry_run=dry_run,
                confirm=not dry_run,
                note=f"autopilot:buy:score={brief.score:.0f}:{actor}",
            )
            try:
                res = await self._desk.execute(req)
                buys.append(
                    {
                        "symbol": sym,
                        "notional": notional,
                        "score": brief.score,
                        "confidence": brief.confidence,
                        "ok": res.ok,
                        "message": res.message,
                        "dry_run": res.dry_run,
                    }
                )
                open_notional += notional
            except Exception as exc:
                buys.append({"symbol": sym, "error": str(exc)})

        reason = None
        if not buys and not sells:
            if not candidates:
                reason = "no_buy_signal"
            elif slots <= 0:
                reason = "max_open_reached"

        return {
            "budget": round(desk_budget, 2),
            "open_before": len(open_trades),
            "universe": universe_n,
            "chart_prequalified": prequalified if desk == "crypto" else None,
            "buys": buys,
            "sells": sells,
            "holds": holds,
            "scanned": scanned[:40],
            "min_score": min_score,
            "reason": reason,
            "dry_run": dry_run,
        }
