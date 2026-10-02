"""Multi-asset beta autopilot — capital-aware buys/sells per desk."""

from __future__ import annotations

from typing import Any

from config.settings import get_settings
from domain.multiasset import AssetDeskId, MultiAssetOrderRequest
from services.kill_switch_service import KillSwitchService
from services.multiasset.allocator import allocate
from services.multiasset.desk_service import MultiAssetDeskService
from services.multiasset.desks import DESKS, get_desk
from services.multiasset.paper_broker import get_beta_broker_provider
from services.multiasset.risk_engine import MultiAssetRiskDesk, size_notional_1x, trail_stop
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
        self._risk = MultiAssetRiskDesk(session)

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

        from services.multiasset.paper_broker import MultiAssetNotPaperError, assert_beta_account_is_paper

        try:
            await assert_beta_account_is_paper(self._broker)
        except MultiAssetNotPaperError as exc:
            out["skipped"] = "not_paper"
            out["error"] = str(exc)
            logger.error("multiasset.autopilot.refused_not_paper", error=str(exc))
            return out

        # LIVE kill-switch is read-only (CEO panic). Mesa paper has its own DD kill.
        if await KillSwitchService(self._session).is_active():
            out["skipped"] = "firm_kill_switch_active"
            return out

        dry = bool(getattr(self._settings, "multiasset_autopilot_dry_run", False))
        if not dry and not self._broker.is_configured():
            dry = True
            out["forced_dry_run"] = "broker_unconfigured"
        out["dry_run"] = dry
        out["paper_orders"] = (not dry) and self._broker.is_configured()
        out["broker_base"] = getattr(self._broker, "base_url", "") or ""

        market_open = is_market_open()
        out["market_open"] = market_open
        out["leverage"] = 1.0
        out["paper"] = True

        if not dry:
            try:
                from services.multiasset.paper_ops import cancel_stale_paper_market_buys

                out["stale_market_buys"] = await cancel_stale_paper_market_buys(self._broker)
            except Exception as exc:
                logger.warning("multiasset.stale_buys.failed", error=str(exc))
                out["stale_market_buys"] = {"skipped": "error", "error": str(exc), "cancelled": []}
            try:
                from database.repositories.ops_repository import OpsFlagRepository
                from services.multiasset.crypto_legacy import flatten_before_strategy_a

                out["legacy_flatten"] = await flatten_before_strategy_a(
                    self._broker,
                    tracker=self._tracker,
                    flags=OpsFlagRepository(self._session),
                )
            except Exception as exc:
                logger.warning("crypto.legacy.flatten_failed", error=str(exc))
                out["legacy_flatten"] = {"skipped": "error", "error": str(exc)}

        records = {}
        try:
            for d in ("gold", "forex", "crypto"):
                records[d] = await self._tracker.track_record(desk=d, window_days=90)
        except Exception as exc:
            logger.warning("multiasset.autopilot.records_failed", error=str(exc))
        plan = allocate(market_open=market_open, records=records)
        weights = plan["weights"]
        out["allocation_mode"] = plan["mode"]
        out["director"] = {k: plan[k] for k in ("notes", "profit_factors", "expectancy_pct") if k in plan}

        equity = 0.0
        try:
            capital_preview = await self._capital_snapshot(offhours_crypto=not market_open)
            equity = float(capital_preview.get("equity_usd") or 0)
        except Exception:
            equity = float(getattr(self._settings, "multiasset_fallback_equity", 10_000) or 10_000)
        blocked, why = await self._risk.block_new_buys(equity)
        out["risk"] = await self._risk.snapshot(equity=equity)
        out["new_buys_blocked"] = blocked
        out["block_reason"] = why

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
                    allow_buys=not blocked,
                    equity=float(capital.get("equity_usd") or equity),
                    cash=cash,
                )
                out["desks"][desk_id]["weight"] = weight
            except Exception as exc:
                logger.warning("multiasset.autopilot.desk_failed", desk=desk_id, error=str(exc))
                out["desks"][desk_id] = {"error": str(exc)}

        try:
            await self._risk.record_cycle(
                {
                    "actor": actor,
                    "skipped": out.get("skipped"),
                    "dry_run": dry,
                    "deployable_usd": out.get("deployable_usd"),
                    "block_reason": why,
                    "broker_stops_gtc": False,
                    "desks": {
                        k: (
                            {
                                "buys": len((v or {}).get("buys") or []),
                                "sells": len((v or {}).get("sells") or []),
                                "skipped": (v or {}).get("skipped"),
                                "strategy": (v or {}).get("strategy"),
                                "open_positions": (v or {}).get("open_positions") or [],
                                "broker_stops_gtc": False,
                                "broker_stop": "none",
                            }
                            if k == "crypto"
                            else {
                                "buys": len((v or {}).get("buys") or []),
                                "sells": len((v or {}).get("sells") or []),
                                "skipped": (v or {}).get("skipped"),
                            }
                        )
                        for k, v in (out.get("desks") or {}).items()
                    },
                    "message": why or ("ok" if not blocked else "no_new_buys"),
                    "paper_gap_note": (
                        "Alpaca paper no mide bien los gaps; la protección real es el "
                        "stop al cierre de cada vela 4h."
                    ),
                }
            )
        except Exception as exc:
            logger.warning("multiasset.cycle_record_failed", error=str(exc))

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

    async def _crypto_position_qty(self, symbol: str, trade: Any) -> float | None:
        """Full Alpaca qty. Never invent a $25 notional."""
        want = (symbol or "").upper().replace("/", "").replace("-", "")
        try:
            positions = await self._broker.get_positions()
            for p in positions or []:
                raw = str(p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", "") or "")
                key = raw.upper().replace("/", "").replace("-", "")
                if key == want or key.endswith(want):
                    q = float(p.get("qty") if isinstance(p, dict) else getattr(p, "qty", 0) or 0)
                    if q > 0:
                        return q
        except Exception as exc:
            logger.warning("crypto_a.position_qty_failed", symbol=symbol, error=str(exc))
        fallback = float(getattr(trade, "qty", 0) or 0)
        return fallback if fallback > 0 else None

    async def _crypto_alert(self, flags: Any, *, kind: str, symbol: str, detail: str) -> None:
        payload = {
            "at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "kind": kind,
            "symbol": symbol,
            "detail": str(detail)[:800],
            "paper": True,
        }
        logger.warning("crypto_a.alert", **payload)
        if flags is None:
            return
        try:
            await flags.set_json("crypto_strategy_a_alert", payload)
        except Exception:
            pass

    async def _crypto_market_sell(
        self,
        symbol: str,
        trade: Any,
        *,
        dry_run: bool,
        actor: str,
        reason: str,
        qty: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        sell_qty = float(qty) if qty is not None else await self._crypto_position_qty(symbol, trade)
        if sell_qty is None or sell_qty <= 0:
            return {
                "symbol": symbol,
                "reason": reason,
                "ok": False,
                "error": "qty_unknown",
                "keep_state": True,
            }
        req = MultiAssetOrderRequest(
            desk="crypto",
            symbol=symbol,
            side="sell",
            qty=sell_qty,
            notional=None,
            dry_run=dry_run,
            confirm=not dry_run,
            note=f"autopilot:{reason}:{actor}",
            client_order_id=client_order_id,
        )
        try:
            res = await self._desk.execute(req)
            return {
                "symbol": symbol,
                "reason": reason,
                "ok": bool(res.ok),
                "message": res.message,
                "qty": sell_qty,
                "software_stop": True,
                "keep_state": not bool(res.ok),
            }
        except Exception as exc:
            return {
                "symbol": symbol,
                "reason": reason,
                "ok": False,
                "error": str(exc),
                "qty": sell_qty,
                "keep_state": True,
            }

    async def _run_crypto_strategy_a(
        self,
        *,
        desk_budget: float,
        dry_run: bool,
        allow_buys: bool,
        equity: float,
        cash: float,
        actor: str,
    ) -> dict[str, Any]:
        """Strategy A only: eligibility file + Riesgo limits + software stops."""
        from services.multiasset.crypto_eligibility import (
            EligibilityClosed,
            approved_rows,
            load_approved_universe,
            load_eligibility,
            median_spread_bps,
        )
        from services.multiasset.crypto_filters import build_gate_report, screen_symbol
        from services.multiasset.crypto_risk import (
            CryptoBook,
            MAX_CRYPTO_EQUITY_PCT,
            cluster_symbols,
            daily_weekly_pause,
            entry_spread_ok,
            group_id_for,
            kill_from_allocation_peak,
            per_name_caps,
            size_crypto_order,
            spread_bps,
        )
        from services.multiasset.crypto_obs import position_stop_fields
        from services.multiasset.crypto_universe import _normalize_alpaca_symbol
        from services.multiasset.strategy_a import (
            arm_post_stop_block,
            catch_up_exits,
            rebuild_highest_close,
            should_rebalance,
            signal_for_symbol,
            stop_is_tradable,
            update_post_stop_block,
            vol_weight_notional,
        )

        out: dict[str, Any] = {
            "strategy": "A",
            "combo": 9,
            "software_stops": True,
            "broker_stops_gtc": False,
            "broker_stop": "none",
            "tp": None,
            "budget": round(desk_budget, 2),
            "buys": [],
            "sells": [],
            "holds": [],
            "scanned": [],
            "open_positions": [],
            "paper": True,
            "dry_run": dry_run,
            "paper_gap_note": (
                "Alpaca paper no mide bien los gaps; la protección real es el "
                "stop al cierre de cada vela 4h."
            ),
        }
        approved = load_approved_universe()
        try:
            elig = load_eligibility()
            rows = approved_rows(elig)
        except EligibilityClosed:
            elig = {"version": "default-universe", "approved": [{"symbol": s} for s in approved]}
            rows = [{"symbol": s, "on_approved_universe": True} for s in approved]
        row_by = {r.get("symbol"): r for r in rows}
        rows = []
        for s in approved:
            item = dict(row_by.get(s) or {"symbol": s})
            item["on_approved_universe"] = True
            rows.append(item)
        out["eligibility_version"] = elig.get("version")
        out["approved"] = approved
        min_adv = float(getattr(self._settings, "crypto_min_adv_usd", 1_000_000) or 0)
        min_oos = int(getattr(self._settings, "crypto_min_oos_trades", 20) or 20)
        max_pos = int(getattr(self._settings, "crypto_max_positions", 6) or 6)

        tradable: set[str] = set()
        try:
            assets = await self._broker.list_crypto_assets() if self._broker.is_configured() else []
            for a in assets or []:
                if not isinstance(a, dict) or a.get("tradable") is False:
                    continue
                ns = _normalize_alpaca_symbol(str(a.get("symbol") or ""))
                if ns:
                    tradable.add(ns)
        except Exception as exc:
            logger.warning("crypto.a.assets_failed", error=str(exc))

        open_trades = await self._tracker.list_open(desk="crypto")
        open_by_sym = {t.symbol: t for t in open_trades}

        screened_rows: list[dict] = []
        quotes: dict[str, dict] = {}
        quote_syms = [r["symbol"] for r in rows] if allow_buys else list(open_by_sym.keys())
        for sym in quote_syms:
            try:
                from agents.multiasset import quote_symbol

                q = await quote_symbol(sym)
                quotes[sym] = q or {}
            except Exception:
                quotes[sym] = {}
        if allow_buys:
            for row in rows:
                sym = row["symbol"]
                q = quotes.get(sym) or {}
                last = float(q.get("current_price") or 0) or None
                live = spread_bps(q.get("bid"), q.get("ask"), last)
                adv = row.get("median_adv_usd") or row.get("adv_usd")
                try:
                    adv_f = float(adv) if adv is not None else None
                except (TypeError, ValueError):
                    adv_f = None
                tradable_ok = (not tradable) or (sym in tradable)
                screened_rows.append(
                    screen_symbol(
                        row,
                        min_adv_usd=min_adv,
                        min_trades=min_oos,
                        live_spread_bps=live,
                        adv_usd=adv_f,
                        tradable=tradable_ok,
                    )
                )
        gate_report = build_gate_report(screened_rows) if screened_rows else {
            "paper": True,
            "passed_symbols": [],
            "passed": [],
            "rejected": [],
            "runtime_must_not_recompute_oos": True,
        }
        out["gate_report"] = gate_report
        passed_set = set(gate_report["passed_symbols"])

        import asyncio
        from datetime import datetime, timezone

        import pandas as pd
        from database.repositories.ops_repository import OpsFlagRepository
        from services.multiasset.risk_engine import calendar_day_key, iso_week_key

        from services.multiasset.crypto_cycle_lock import (
            acquire_cycle_lease,
            allocate_sa9_client_order_id,
            release_cycle_lease,
            replica_id,
        )

        flags = OpsFlagRepository(self._session)

        async def _cid(sym: str, candle, action: str) -> str:
            key, _n = await allocate_sa9_client_order_id(
                flags, sym, action, candle, broker=self._broker
            )
            return key

        state = await flags.get_json("crypto_strategy_a_state")
        pos_state: dict[str, Any] = dict(state.get("positions") or {})
        blocks: dict[str, Any] = dict(state.get("blocks") or {})
        now = datetime.now(timezone.utc)
        ohlc_cache: dict[str, Any] = {}
        owner = replica_id()
        out["replica_id"] = owner
        got_lease, lease = await acquire_cycle_lease(
            flags, owner=owner, now=now, session=self._session
        )
        if not got_lease:
            out["skipped"] = "cycle_lease_held"
            out["lease"] = lease
            return out

        async def _ohlc(sym: str):
            if sym not in ohlc_cache:
                from services.multiasset.engine_bars import load_strategy_a_4h

                ohlc_cache[sym] = await load_strategy_a_4h(sym, now=now)
            return ohlc_cache[sym]

        def _obs_row(sym: str, st: dict[str, Any]) -> dict[str, Any]:
            return {
                "symbol": sym,
                **position_stop_fields(
                    stop_evaluated_at=st.get("stop_evaluated_at"),
                    candle_close=st.get("candle_close"),
                    stop_px=st.get("stop_px"),
                    broker_stop=st.get("broker_stop") or "none",
                ),
            }

        async def _persist_state() -> None:
            payload = {
                "positions": pos_state,
                "blocks": blocks,
                "at": now.isoformat(),
                "broker_stops_gtc": False,
                "broker_stop": "none",
                "combo": 9,
                "last_evaluated_candle": out.get("last_evaluated_candle"),
                "eval_history": list(out.get("eval_history") or [])[-6:],
                "missed_candles": out.get("missed_candles") or 0,
                "candles_behind": out.get("candles_behind") or 0,
                "replica_id": out.get("replica_id"),
            }
            await flags.set_json("crypto_strategy_a_state", payload)
            out["open_positions"] = [_obs_row(k, v) for k, v in pos_state.items()]

        eval_history: list[dict[str, Any]] = list(state.get("eval_history") or [])
        missed_total = 0
        last_eval_cursor = state.get("last_evaluated_candle")
        # Catch-up: every closed 4h since last_evaluated_candle, in order (engine.py).
        for sym, trade in list(open_by_sym.items()):
            delisted = bool(tradable) and (
                _normalize_alpaca_symbol(sym) not in tradable and sym not in tradable
            )
            prev = dict(pos_state.get(sym) or {})
            if delisted:
                cid = await _cid(sym, last_eval_cursor, "delist")
                sold = await self._crypto_market_sell(
                    sym,
                    trade,
                    dry_run=dry_run,
                    actor=actor,
                    reason="delisted_close_now",
                    client_order_id=cid,
                )
                out["sells"].append(sold)
                if sold.get("ok"):
                    open_by_sym.pop(sym, None)
                    pos_state.pop(sym, None)
                else:
                    await self._crypto_alert(
                        flags, kind="sell_failed", symbol=sym, detail=str(sold.get("error") or sold)
                    )
                continue
            try:
                df = await _ohlc(sym)
            except Exception as exc:
                df = None
                await self._crypto_alert(flags, kind="data_noop", symbol=sym, detail=str(exc))
            if df is None or getattr(df, "empty", True):
                await self._crypto_alert(
                    flags, kind="data_noop", symbol=sym, detail="empty_4h_keep_state"
                )
                out["holds"].append(sym)
                continue
            persisted_high = prev.get("highest_close") or prev.get("max_close")
            rebuilt, src = rebuild_highest_close(
                df,
                entry_ts=getattr(trade, "opened_at", None) or prev.get("opened_at"),
                persisted=float(persisted_high) if persisted_high else None,
            )
            high0 = float(rebuilt or persisted_high or trade.entry_price or 0)
            cup = catch_up_exits(
                df,
                last_evaluated_open=last_eval_cursor or prev.get("last_evaluated_candle"),
                highest_close=high0,
                stop_px=float(prev.get("stop_px") or trade.stop_hint or 0) or None,
                now=now,
                block=blocks.get(sym),
            )
            if not cup.get("data_ok"):
                await self._crypto_alert(
                    flags, kind="data_noop", symbol=sym, detail="catch_up_data_insufficient"
                )
                out["holds"].append(sym)
                continue
            missed_total += int(cup.get("missed_candles") or 0)
            for ev in cup.get("evals") or []:
                eval_history.append({"symbol": sym, **ev})
            if cup.get("last_evaluated_candle"):
                last_eval_cursor = cup["last_evaluated_candle"]
            last_ev = (cup.get("evals") or [{}])[-1] if cup.get("evals") else {}
            st = {
                **prev,
                "stop_evaluated_at": last_ev.get("candle_open") or cup.get("last_evaluated_candle"),
                "clock_evaluated_at": now.isoformat(),
                "candle_close": last_ev.get("candle_close"),
                "stop_px": cup.get("stop_px"),
                "highest_close": cup.get("highest_close"),
                "max_close": cup.get("highest_close"),
                "broker_stop": "none",
                "atr": last_ev.get("atr"),
                "state_source": src,
                "last_evaluated_candle": cup.get("last_evaluated_candle"),
            }
            pos_state[sym] = st
            if cup.get("stop_px"):
                try:
                    await self._tracker.update_stop(
                        desk="crypto",
                        symbol=sym,
                        stop=float(cup["stop_px"]),
                        peak=float(cup.get("highest_close") or 0) or None,
                        extra_meta={
                            "stop_evaluated_at": st.get("stop_evaluated_at"),
                            "candle_close": st.get("candle_close"),
                            "broker_stop": "none",
                            "state_source": src,
                        },
                    )
                except Exception:
                    pass
            hit_bar = cup.get("hit_bar")
            if cup.get("hit") and hit_bar:
                cid = await _cid(sym, hit_bar.get("candle_open"), "chand")
                sold = await self._crypto_market_sell(
                    sym,
                    trade,
                    dry_run=dry_run,
                    actor=actor,
                    reason="software_chandelier_stop",
                    client_order_id=cid,
                )
                sold["late"] = True
                sold["eval"] = {k: hit_bar.get(k) for k in ("candle_open", "candle_close", "late", "stop_px")}
                out["sells"].append(sold)
                if sold.get("ok"):
                    open_by_sym.pop(sym, None)
                    pos_state.pop(sym, None)
                    s_now = float(prev.get("S") or 0)
                    try:
                        btc_df = await _ohlc("BTC/USD")
                        sig_s = await signal_for_symbol(
                            sym, frames={"4h": df, "btc_4h": btc_df}, now=now
                        )
                        s_now = float(sig_s.S) if sig_s.S == sig_s.S else s_now
                    except Exception:
                        pass
                    blocks[sym] = arm_post_stop_block(s_now)
                else:
                    await self._crypto_alert(
                        flags,
                        kind="sell_failed_keep_state",
                        symbol=sym,
                        detail=str(sold.get("error") or sold.get("message") or sold),
                    )
            else:
                out["holds"].append(sym)

        out["last_evaluated_candle"] = last_eval_cursor
        out["eval_history"] = eval_history[-6:]
        out["missed_candles"] = missed_total
        out["candles_behind"] = missed_total

        # Mark-to-market + realized wealth (never cost). Selling a name must not stick kill.
        crypto_usd = 0.0
        for t in open_by_sym.values():
            last_px = float((quotes.get(t.symbol) or {}).get("current_price") or 0) or float(
                t.entry_price or 0
            )
            crypto_usd += last_px * float(t.qty or 0)
        realized = 0.0
        for sold in out["sells"]:
            if not sold.get("ok"):
                continue
            try:
                realized += float(sold.get("pnl_usd") or 0)
            except (TypeError, ValueError):
                pass
        eq = float(equity or desk_budget or 0)
        allocation = eq * MAX_CRYPTO_EQUITY_PCT / 100.0
        mark = await flags.get_json("crypto_strategy_a_risk")
        realized += float(mark.get("realized_pnl_usd") or 0)
        wealth = crypto_usd + realized
        peak = max(float(mark.get("peak_wealth_usd") or mark.get("peak_crypto_usd") or 0), wealth)
        day = calendar_day_key()
        week = iso_week_key()
        rolling_24h_start = float(mark.get("rolling_24h_wealth") or wealth)
        rolling_7d_start = float(mark.get("rolling_7d_wealth") or wealth)
        t24 = mark.get("rolling_24h_at")
        t7 = mark.get("rolling_7d_at")
        try:
            if t24:
                from datetime import datetime as _dt

                age24 = (now - _dt.fromisoformat(str(t24).replace("Z", "+00:00"))).total_seconds()
                if age24 > 24 * 3600:
                    rolling_24h_start = wealth
                    mark["rolling_24h_at"] = now.isoformat()
            else:
                mark["rolling_24h_at"] = now.isoformat()
        except Exception:
            mark["rolling_24h_at"] = now.isoformat()
            rolling_24h_start = wealth
        try:
            if t7:
                from datetime import datetime as _dt

                age7 = (now - _dt.fromisoformat(str(t7).replace("Z", "+00:00"))).total_seconds()
                if age7 > 7 * 24 * 3600:
                    rolling_7d_start = wealth
                    mark["rolling_7d_at"] = now.isoformat()
            else:
                mark["rolling_7d_at"] = now.isoformat()
        except Exception:
            mark["rolling_7d_at"] = now.isoformat()
            rolling_7d_start = wealth
        day_pnl_pct = 0.0
        week_pnl_pct = 0.0
        if allocation > 0:
            day_pnl_pct = (wealth - rolling_24h_start) / allocation * 100.0
            week_pnl_pct = (wealth - rolling_7d_start) / allocation * 100.0
        from services.multiasset.crypto_risk import (
            accum_brake_triggered,
            loss_streak_pause,
            wealth_drawdown_pct,
        )

        dd_pct = wealth_drawdown_pct(peak=peak, wealth=wealth, allocation=allocation)
        mark["peak_wealth_usd"] = peak
        mark["peak_crypto_usd"] = peak
        mark["crypto_usd"] = crypto_usd
        mark["realized_pnl_usd"] = realized
        mark["wealth_usd"] = wealth
        mark["dd_pct"] = round(dd_pct, 4)
        mark["day_pnl_pct"] = round(day_pnl_pct, 4)
        mark["week_pnl_pct"] = round(week_pnl_pct, 4)
        mark["rolling_24h_wealth"] = rolling_24h_start
        mark["rolling_7d_wealth"] = rolling_7d_start
        await flags.set_json("crypto_strategy_a_risk", mark)
        await flags.set_json(
            "crypto_strategy_a_daily_screen",
            {
                **gate_report,
                "day": day,
                "version": elig.get("version"),
                "at": now.isoformat(),
                "min_adv_usd": min_adv,
                "min_oos_trades": min_oos,
            },
        )
        out["crypto_usd"] = round(crypto_usd, 2)
        out["allocation_usd"] = round(allocation, 2)

        if kill_from_allocation_peak(
            peak_crypto_usd=peak, crypto_usd=wealth, allocation_usd=allocation
        ):
            out["skipped"] = "crypto_kill_10pct_allocation"
            out["new_buys_blocked"] = True
            mark["kill_active"] = True
            allow_buys = False
        paused, pause_why = daily_weekly_pause(day_pnl_pct=day_pnl_pct, week_pnl_pct=week_pnl_pct)
        if paused:
            out["buys_paused"] = pause_why
            allow_buys = False
        if accum_brake_triggered(
            dd_pct, brake_pct=float(getattr(self._settings, "crypto_accum_brake_pct", 5.0) or 5.0)
        ):
            out["buys_paused"] = out.get("buys_paused") or f"accum_brake_{dd_pct:.2f}pct"
            allow_buys = False
        streak_paused, streak_why = loss_streak_pause(
            mark.get("losses"),
            now=now,
            n=int(getattr(self._settings, "crypto_loss_streak_n", 3) or 3),
            hours=float(getattr(self._settings, "crypto_loss_streak_pause_hours", 24) or 24),
        )
        if streak_paused:
            out["buys_paused"] = streak_why
            allow_buys = False

        name_notional: dict[str, float] = {}
        name_risk: dict[str, float] = {}
        open_risk = 0.0
        for t in open_by_sym.values():
            last_px = float((quotes.get(t.symbol) or {}).get("current_price") or 0) or float(
                t.entry_price or 0
            )
            n = last_px * float(t.qty or 0)
            stop_now = float((pos_state.get(t.symbol) or {}).get("stop_px") or t.stop_hint or 0)
            r = 0.0
            if last_px and stop_now and t.qty:
                r = max(0.0, (last_px - stop_now) * float(t.qty))
            name_notional[t.symbol] = name_notional.get(t.symbol, 0.0) + n
            name_risk[t.symbol] = name_risk.get(t.symbol, 0.0) + r
            open_risk += r
        from services.multiasset.crypto_market_stats import (
            FLAG_WEEKLY,
            adv_for_symbol,
            build_weekly_snapshot_from_daily,
            corr_from_4h,
            corr_pairs_from_snapshot,
            fetch_alpaca_crypto_daily,
        )

        weekly = await flags.get_json(FLAG_WEEKLY)
        if weekly.get("week_key") != week:
            dailies = await fetch_alpaca_crypto_daily(approved, now=now)
            weekly = build_weekly_snapshot_from_daily(
                dailies, week_key=week, expected=approved
            )
            weekly["at"] = now.isoformat()
            # Empty fetch (no keys / API fail) must not stamp the week — retry next cycle.
            if dailies or not approved:
                await flags.set_json(FLAG_WEEKLY, weekly)
            else:
                weekly["week_key"] = None
                weekly["retry"] = True
        frames_4h: dict[str, Any] = {}
        for s in approved:
            try:
                frames_4h[s] = await _ohlc(s)
            except Exception:
                continue
        corr4 = corr_from_4h(frames_4h)
        if corr4:
            weekly = dict(weekly or {})
            weekly["corr"] = corr4
            weekly["corr_source"] = "4h_90d"
        out["weekly_market"] = {
            "week_key": weekly.get("week_key"),
            "source": weekly.get("source"),
            "corr_source": weekly.get("corr_source") or "daily_fallback",
            "adv_symbols": list((weekly.get("adv_usd") or {}).keys()),
            "rejected": weekly.get("rejected") or [],
        }

        groups = cluster_symbols(
            corr_pairs_from_snapshot(weekly),
            list(open_by_sym.keys()) + approved,
        )
        group_notional: dict[str, float] = {}
        group_risk: dict[str, float] = {}
        membership: dict[str, str] = {}
        for t in open_by_sym.values():
            gid = group_id_for(t.symbol, groups)
            membership[t.symbol] = gid
            n = name_notional.get(t.symbol, 0.0)
            r = name_risk.get(t.symbol, 0.0)
            group_notional[gid] = group_notional.get(gid, 0.0) + n
            group_risk[gid] = group_risk.get(gid, 0.0) + r
        book = CryptoBook(
            equity=eq,
            crypto_notional=crypto_usd,
            open_risk_usd=open_risk,
            n_positions=len(open_by_sym),
            name_notional=name_notional,
            name_risk=name_risk,
            group_notional=group_notional,
            group_risk=group_risk,
            membership=membership,
        )

        # Exits (S=0 / rebalance-down) always run — last closed bar only.
        # Rebalance-up and new entries: last bar + allow_buys. Never replay recovered signals.
        pending_rebalance_up: list[tuple[str, Any, Any, float, float]] = []
        for sym, trade in list(open_by_sym.items()):
            try:
                sig = await signal_for_symbol(
                    sym,
                    frames={"4h": frames_4h.get(sym), "btc_4h": frames_4h.get("BTC/USD")},
                    now=now,
                )
            except Exception as exc:
                out["scanned"].append({"symbol": sym, "skip": f"signal_failed:{exc}"})
                continue
            if not (sig.extras or {}).get("data_ok", True) or (sig.S != sig.S):
                await self._crypto_alert(
                    flags, kind="data_noop", symbol=sym, detail="signal_data_insufficient_no_flatten"
                )
                continue
            blocks[sym] = update_post_stop_block(blocks.get(sym), sig.S)
            st = dict(pos_state.get(sym) or {})
            st["S"] = sig.S
            pos_state[sym] = st
            last = float((quotes.get(sym) or {}).get("current_price") or 0) or float(trade.entry_price or 0)
            if last <= 0:
                continue
            current_n = last * float(trade.qty or 0)
            name_cap, _ = per_name_caps(sym, eq)
            target = vol_weight_notional(s=sig.S, vol_30d=sig.vol_30d, name_cap_notional=name_cap)
            prev_s = st.get("last_S")
            candle_open = (sig.extras or {}).get("candle_ts") or last_eval_cursor
            if sig.S <= 1e-12:
                cid = await _cid(sym, candle_open, "s0")
                sold = await self._crypto_market_sell(
                    sym,
                    trade,
                    dry_run=dry_run,
                    actor=actor,
                    reason="S_zero_flatten",
                    client_order_id=cid,
                )
                out["sells"].append(sold)
                if sold.get("ok"):
                    open_by_sym.pop(sym, None)
                    pos_state.pop(sym, None)
                else:
                    await self._crypto_alert(
                        flags, kind="sell_failed_keep_state", symbol=sym, detail=str(sold)
                    )
                continue
            if not should_rebalance(current_n, target, prev_s, sig.S):
                st["last_S"] = sig.S
                pos_state[sym] = st
                continue
            delta = target - current_n
            st["last_S"] = sig.S
            pos_state[sym] = st
            if delta < -1e-6 and abs(delta) >= 10:
                last_px = last
                sell_qty = abs(delta) / last_px if last_px > 0 else float(trade.qty or 0)
                full_qty = float(trade.qty or 0)
                flatten = sell_qty >= full_qty * 0.98 or (full_qty - sell_qty) * last_px < 10
                cid = await _cid(sym, candle_open, "rebdown")
                sold = await self._crypto_market_sell(
                    sym,
                    trade,
                    dry_run=dry_run,
                    actor=actor,
                    reason="rebalance_down",
                    qty=None if flatten else sell_qty,
                    client_order_id=cid,
                )
                out["sells"].append({"symbol": sym, "reason": "rebalance_down", "ok": sold.get("ok")})
                if sold.get("ok") and flatten:
                    open_by_sym.pop(sym, None)
                    pos_state.pop(sym, None)
                elif sold.get("ok"):
                    book.crypto_notional = max(0.0, book.crypto_notional + delta)
                else:
                    await self._crypto_alert(
                        flags, kind="sell_failed_keep_state", symbol=sym, detail=str(sold)
                    )
            elif delta > 10:
                pending_rebalance_up.append((sym, trade, sig, last, delta))

        if allow_buys:
            for sym, trade, sig, last, delta in pending_rebalance_up:
                st = dict(pos_state.get(sym) or {})
                stop_px = float(st.get("stop_px") or sig.stop_px or 0)
                if stop_px <= 0 or not stop_is_tradable(last, stop_px):
                    continue
                med = median_spread_bps(sym, elig)
                q = quotes.get(sym) or {}
                live = spread_bps(q.get("bid"), q.get("ask"), last)
                ok_sp, why_sp = entry_spread_ok(sym, live_bps=live, median_bps=med)
                if not ok_sp:
                    out["scanned"].append({"symbol": sym, "skip": why_sp})
                    continue
                adv_rebal, why_rebal = adv_for_symbol(weekly, sym)
                if adv_rebal is None:
                    out["scanned"].append({"symbol": sym, "skip": why_rebal or "history_lt_30d"})
                    continue
                n_trades = await self._tracker.count_symbol_trades(desk="crypto", symbol=sym)
                add_risk = max(0.0, (last - stop_px) * (delta / last)) if last > 0 else 0.0
                book.open_risk_usd += add_risk
                add, info = size_crypto_order(
                    symbol=sym,
                    equity=eq,
                    entry=last,
                    stop=stop_px,
                    book=book,
                    n_trades=n_trades,
                    median_adv_usd=adv_rebal,
                    groups=groups,
                    max_positions=max_pos + 1,
                    s_signal=sig.S,
                    vol_30d=sig.vol_30d,
                    live_spread_bps=live,
                )
                add = min(add, delta)
                if add < 50.0 - 1e-9:
                    book.open_risk_usd = max(0.0, book.open_risk_usd - add_risk)
                    out["scanned"].append({"symbol": sym, "skip": info.get("reason") or "rebalance_lt_50"})
                    continue
                if add <= 0:
                    book.open_risk_usd = max(0.0, book.open_risk_usd - add_risk)
                    out["scanned"].append({"symbol": sym, "skip": info.get("reason") or "rebalance_too_small"})
                    continue
                candle_open = (sig.extras or {}).get("candle_ts") or last_eval_cursor
                req = MultiAssetOrderRequest(
                    desk="crypto",
                    symbol=sym,
                    side="buy",
                    notional=add,
                    dry_run=dry_run,
                    confirm=not dry_run,
                    note=f"strategy_a:rebalance:{actor}",
                    client_order_id=await _cid(sym, candle_open, "rebup"),
                )
                try:
                    res = await self._desk.execute(req)
                    out["buys"].append({"symbol": sym, "notional": add, "ok": res.ok, "reason": "rebalance_up"})
                    if res.ok:
                        book.crypto_notional += add
                        book.name_notional[sym] = book.name_notional.get(sym, 0.0) + add
                    else:
                        book.open_risk_usd = max(0.0, book.open_risk_usd - add_risk)
                except Exception as exc:
                    book.open_risk_usd = max(0.0, book.open_risk_usd - add_risk)
                    out["buys"].append({"symbol": sym, "error": str(exc), "reason": "rebalance_up"})

        if not allow_buys:
            out["reason"] = "new_buys_blocked"
            await _persist_state()
            await release_cycle_lease(flags, owner, session=self._session)
            return out

        for row in rows:
            sym = row["symbol"]
            if sym in open_by_sym:
                continue
            if sym not in passed_set:
                why = next((r.get("reasons") for r in screened_rows if r.get("symbol") == sym), ["gate"])
                out["scanned"].append({"symbol": sym, "skip": why[0] if why else "gate"})
                continue
            if tradable and sym not in tradable:
                out["scanned"].append({"symbol": sym, "skip": "not_tradable_alpaca"})
                continue
            med = median_spread_bps(sym, elig)
            q = quotes.get(sym) or {}
            last = float(q.get("current_price") or 0) or None
            live = spread_bps(q.get("bid"), q.get("ask"), last)
            ok_sp, why_sp = entry_spread_ok(sym, live_bps=live, median_bps=med)
            if not ok_sp:
                out["scanned"].append({"symbol": sym, "skip": why_sp})
                continue
            try:
                sig = await signal_for_symbol(sym)
            except Exception as exc:
                out["scanned"].append({"symbol": sym, "skip": f"signal_failed:{exc}"})
                continue
            blocks[sym] = update_post_stop_block(blocks.get(sym), sig.S)
            if (blocks.get(sym) or {}).get("blocked"):
                out["scanned"].append({"symbol": sym, "skip": "post_stop_block"})
                continue
            if sig.side != "buy" or not sig.stop_px or not last or sig.S <= 1e-12:
                out["scanned"].append({"symbol": sym, "rec": sig.side, "skip": sig.reason, "S": sig.S})
                continue
            if not stop_is_tradable(float(last), float(sig.stop_px)):
                out["scanned"].append({"symbol": sym, "skip": "invalid_stop_round"})
                continue
            n_trades = await self._tracker.count_symbol_trades(desk="crypto", symbol=sym)
            adv_f, why_h = adv_for_symbol(weekly, sym)
            if adv_f is None:
                out["scanned"].append({"symbol": sym, "skip": why_h})
                continue
            notional, info = size_crypto_order(
                symbol=sym,
                equity=eq,
                entry=float(last),
                stop=float(sig.stop_px),
                book=book,
                n_trades=n_trades,
                median_adv_usd=adv_f,
                groups=groups,
                max_positions=max_pos,
                s_signal=sig.S,
                vol_30d=sig.vol_30d,
            )
            if notional <= 0:
                out["scanned"].append({"symbol": sym, "skip": info.get("reason")})
                continue
            req = MultiAssetOrderRequest(
                desk="crypto",
                symbol=sym,
                side="buy",
                notional=notional,
                dry_run=dry_run,
                confirm=not dry_run,
                note=f"strategy_a:{sig.reason}:{actor}",
                client_order_id=await _cid(
                    sym, (sig.extras or {}).get("candle_ts") or last_eval_cursor, "buy"
                ),
            )
            try:
                res = await self._desk.execute(req)
                if res.ok:
                    open_t = await self._tracker.get_open("crypto", sym)
                    if open_t:
                        await self._tracker.update_stop(
                            desk="crypto",
                            symbol=sym,
                            stop=float(sig.stop_px),
                            peak=float(last),
                            extra_meta={
                                "broker_stop": "none",
                                "S": sig.S,
                                "candle_close": float((sig.extras or {}).get("candle_close") or last),
                                "stop_evaluated_at": now.isoformat(),
                            },
                        )
                    book.crypto_notional += notional
                    book.n_positions += 1
                    book.name_notional[sym] = book.name_notional.get(sym, 0.0) + notional
                    risk_usd = (float(last) - float(sig.stop_px)) / float(last) * notional
                    book.open_risk_usd += risk_usd
                    book.name_risk[sym] = book.name_risk.get(sym, 0.0) + risk_usd
                    gid = group_id_for(sym, groups)
                    book.group_notional[gid] = book.group_notional.get(gid, 0.0) + notional
                    book.group_risk[gid] = book.group_risk.get(gid, 0.0) + risk_usd
                    pos_state[sym] = {
                        "stop_px": sig.stop_px,
                        "highest_close": float((sig.extras or {}).get("candle_close") or last),
                        "candle_close": float((sig.extras or {}).get("candle_close") or last),
                        "stop_evaluated_at": now.isoformat(),
                        "broker_stop": "none",
                        "S": sig.S,
                        "last_S": sig.S,
                        "pending_exit": False,
                    }
                out["buys"].append(
                    {
                        "symbol": sym,
                        "notional": notional,
                        "stop": sig.stop_px,
                        "S": sig.S,
                        "ok": res.ok,
                        "message": res.message,
                        "ramp": info.get("ramp"),
                        "software_stop": True,
                        "broker_stop": "none",
                    }
                )
            except Exception as exc:
                out["buys"].append({"symbol": sym, "error": str(exc)})
            if book.n_positions >= max_pos:
                break

        out["open_after"] = book.n_positions
        out["open_risk_usd"] = round(book.open_risk_usd, 4)
        await _persist_state()
        if not out["buys"] and not out["sells"]:
            out["reason"] = out.get("reason") or "no_buy_signal"
        await release_cycle_lease(flags, owner, session=self._session)
        return out

    async def crypto_catchup_on_wake(self, *, actor: str = "wake") -> dict[str, Any]:
        """On wake / last-cycle GET: catch-up closed 4h bars (exits only)."""
        if not getattr(self._settings, "crypto_strategy_a_enabled", True):
            return {"skipped": "strategy_a_disabled"}
        if not getattr(self._settings, "multiasset_beta_enabled", False):
            return {"skipped": "multiasset_beta_disabled"}
        equity = 0.0
        cash = 0.0
        try:
            snap = await self._capital_snapshot(offhours_crypto=True)
            equity = float(snap.get("equity_usd") or 0)
            cash = float(snap.get("cash_usd") or 0)
        except Exception:
            pass
        dry = bool(getattr(self._settings, "multiasset_autopilot_dry_run", False))
        if not dry and not self._broker.is_configured():
            dry = True
        return await self._run_crypto_strategy_a(
            desk_budget=0.0,
            dry_run=dry,
            allow_buys=False,
            equity=equity,
            cash=cash,
            actor=actor,
        )

    async def _run_desk(
        self,
        desk: AssetDeskId,
        *,
        desk_budget: float,
        dry_run: bool,
        market_open: bool,
        actor: str,
        allow_buys: bool = True,
        equity: float = 0.0,
        cash: float = 0.0,
    ) -> dict[str, Any]:
        if desk == "crypto" and bool(getattr(self._settings, "crypto_strategy_a_enabled", True)):
            return await self._run_crypto_strategy_a(
                desk_budget=desk_budget,
                dry_run=dry_run,
                allow_buys=allow_buys,
                equity=equity,
                cash=cash,
                actor=actor,
            )
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
                # Trail: ratchet stop after +1R (software) — broker GTC stop remains the floor.
                atr_abs = float(brief.atr or 0)
                trail_mult = float(brief.trail_atr_mult or 0)
                peak = float((trade.meta or {}).get("peak") or trade.entry_price)
                peak = max(peak, float(px))
                init_stop = float(trade.stop_hint or 0)
                if init_stop and atr_abs > 0 and trail_mult > 0:
                    new_stop, moved = trail_stop(
                        entry=float(trade.entry_price),
                        stop=init_stop,
                        peak=peak,
                        price=float(px),
                        trail_atr_abs=atr_abs * trail_mult,
                        arm_r=float(self._risk.policy.trail_arm_r),
                    )
                    if moved and new_stop > init_stop:
                        await self._tracker.update_stop(
                            desk=desk, symbol=sym, stop=new_stop, peak=peak
                        )
                        await self._desk.journal_stop_adjust(
                            desk=desk,
                            symbol=sym,
                            old_stop=init_stop,
                            new_stop=new_stop,
                            reason=f"trailing +1R {init_stop:.4f}→{new_stop:.4f}",
                        )
                        trade = await self._tracker.get_open(desk, sym) or trade
                ret = (float(px) - float(trade.entry_price)) / float(trade.entry_price)
                if trade.stop_hint and float(px) <= float(trade.stop_hint):
                    exit_reason = "stop_hint"
                elif trade.target_hint and float(px) >= float(trade.target_hint) and trail_mult <= 0:
                    # Hard TP only when not trailing (legacy). New book lets winners run.
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

        if not allow_buys:
            return {
                "budget": round(desk_budget, 2),
                "open_before": len(open_trades),
                "universe": len(strategy.symbols),
                "buys": [],
                "sells": sells,
                "holds": holds,
                "scanned": scanned[:40],
                "reason": "new_buys_blocked",
                "dry_run": dry_run,
            }

        # 2) Rank fresh entries — crypto: specialist pre-screen, then full brief
        candidates: list[tuple[float, str, Any]] = []
        symbols_to_brief = list(strategy.symbols)

        if desk == "crypto":
            from agents.multiasset.specialists import CryptoBreakoutSpecialist
            import asyncio

            spec_agent = CryptoBreakoutSpecialist()
            pre: list[tuple[float, str]] = []

            async def _screen(sym: str):
                try:
                    rep = await spec_agent.analyze(sym)
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
            chart_min = float(getattr(self._settings, "multiasset_crypto_specialist_prescreen", 12) or 12)
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
                spec_vote = next(
                    (v for v in brief.votes if v.agent_name == "crypto_breakout_specialist"),
                    None,
                )
                if spec_vote is None or spec_vote.score < 12:
                    row["skip"] = (
                        f"specialist_gate score={spec_vote.score if spec_vote else 'n/a'} (need≥12)"
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
            entry = float(brief.entry_hint or 0)
            stop = float(brief.stop_hint or 0)
            risk_pct = float(self._risk.policy.risk_pct.get(desk, 2.5))
            notional, size_info = size_notional_1x(
                equity=equity or desk_budget,
                cash=cash or desk_budget,
                desk_budget=desk_budget,
                entry=entry,
                stop=stop,
                risk_pct=risk_pct,
                open_notional=open_notional,
                max_leverage=1.0,
            )
            if notional <= 0:
                scanned.append({"symbol": sym, "skip": size_info.get("reason") or "no_budget_room"})
                continue
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
                        "order_id": res.order_id,
                        "status": res.status,
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
