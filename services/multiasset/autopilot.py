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

# Share of the multi-asset sleeve allocated to each desk (sums to 1)
_DESK_WEIGHTS: dict[AssetDeskId, float] = {
    "gold": 0.40,
    "forex": 0.25,
    "crypto": 0.35,
}


class MultiAssetAutopilotService:
    """One cycle: size by capital → manage open risk → brief → buy/sell paper/sim."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()
        self._desk = MultiAssetDeskService(session)
        self._tracker = MultiAssetTradeTracker(session)
        self._broker = get_beta_broker_provider()

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

        dry = bool(getattr(self._settings, "multiasset_autopilot_dry_run", True))
        # Real paper only if broker configured and dry_run flag off
        if not dry and not self._broker.is_configured():
            dry = True
            out["forced_dry_run"] = "broker_unconfigured"

        capital = await self._capital_snapshot()
        out["capital"] = capital
        sleeve = float(capital["sleeve_usd"])
        cash = float(capital["cash_usd"])
        reserve = float(capital["reserve_usd"])
        deployable = max(0.0, min(sleeve, cash - reserve))
        out["deployable_usd"] = round(deployable, 2)

        market_open = is_market_open()
        out["market_open"] = market_open

        for desk_id, weight in _DESK_WEIGHTS.items():
            desk_budget = deployable * weight
            try:
                out["desks"][desk_id] = await self._run_desk(
                    desk_id,
                    desk_budget=desk_budget,
                    dry_run=dry,
                    market_open=market_open,
                    actor=actor,
                )
            except Exception as exc:
                logger.warning("multiasset.autopilot.desk_failed", desk=desk_id, error=str(exc))
                out["desks"][desk_id] = {"error": str(exc)}

        logger.info(
            "multiasset.autopilot.done",
            dry_run=dry,
            deployable=out["deployable_usd"],
            buys=sum(len(d.get("buys") or []) for d in out["desks"].values() if isinstance(d, dict)),
            sells=sum(len(d.get("sells") or []) for d in out["desks"].values() if isinstance(d, dict)),
        )
        return out

    async def _capital_snapshot(self) -> dict[str, float]:
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

        sleeve_pct = float(getattr(self._settings, "multiasset_sleeve_pct", 30.0) or 30.0) / 100.0
        reserve_pct = float(getattr(self._settings, "multiasset_cash_reserve_pct", 15.0) or 15.0) / 100.0
        max_total = float(self._settings.multiasset_beta_max_notional or 500)
        sleeve = min(equity * sleeve_pct, max_total * 3)  # soft ceiling across desks
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
    ) -> float:
        strategy = get_desk(desk)
        room = max(0.0, desk_budget - open_notional)
        cap = min(
            float(strategy.max_notional_usd),
            float(self._settings.multiasset_beta_max_notional or 500),
            room,
        )
        if cap < 15:
            return 0.0
        # Scale 40–100% of cap by score/confidence (portfolio + risk discipline)
        conf = max(0.35, min(1.0, confidence))
        score_f = min(1.0, abs(score) / 40.0)
        frac = 0.4 + 0.6 * (0.5 * conf + 0.5 * score_f)
        # Crypto: smaller default clip (volatility)
        if desk == "crypto":
            frac *= 0.75
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
        max_open = int(getattr(self._settings, "multiasset_max_open_per_desk", 2) or 2)
        min_score = float(getattr(self._settings, "multiasset_min_score_buy", 12) or 12)
        min_conf = float(getattr(self._settings, "multiasset_min_confidence", 0.45) or 0.45)

        buys: list[dict] = []
        sells: list[dict] = []
        holds: list[str] = []

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

        # 2) Rank fresh entries by brief score
        candidates: list[tuple[float, str, Any]] = []
        for item in strategy.symbols:
            if item.symbol in open_by_sym:
                continue
            try:
                brief = await self._desk.brief(desk, item.symbol)
            except Exception:
                continue
            if brief.recommendation != "buy":
                continue
            if brief.score < min_score or (brief.confidence or 0) < min_conf:
                continue
            # Risk agent veto soft: if any vote strongly negative, skip
            riskish = [
                v
                for v in brief.votes
                if "risk" in v.agent_name and v.score <= -20
            ]
            if riskish and brief.score < min_score + 8:
                continue
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
            )
            if notional <= 0:
                break
            qty = None
            # ETF: prefer qty from price when possible
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

        return {
            "budget": round(desk_budget, 2),
            "open_before": len(open_trades),
            "buys": buys,
            "sells": sells,
            "holds": holds,
            "dry_run": dry_run,
        }
