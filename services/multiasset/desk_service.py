"""Multi-asset beta desk orchestration — brief, execute, status, journal."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.multiasset import AGENT_REGISTRY, build_agents, quote_symbol
from config.settings import get_settings
from database.models import MultiAssetJournalORM, utc_now
from domain.multiasset import (
    AgentVote,
    AssetDeskId,
    DeskBrief,
    DeskStatus,
    MultiAssetJournalEntry,
    MultiAssetOrderRequest,
    MultiAssetOrderResult,
)
from services.multiasset.desks import (
    DESKS,
    desk_symbols,
    get_desk,
    normalize_symbol,
    same_symbol,
    set_crypto_symbols,
)
from services.multiasset.crypto_universe import resolve_crypto_universe
from services.multiasset.paper_broker import get_beta_broker_provider
from services.multiasset.trade_tracker import MultiAssetTradeTracker
from utils.logging import get_logger

logger = get_logger(__name__)


def _label(agent_name: str) -> str:
    inst = AGENT_REGISTRY.get(agent_name)
    if inst and hasattr(inst, "label_es"):
        return str(getattr(inst, "label_es"))
    return agent_name.replace("_", " ")


class MultiAssetDeskService:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._settings = get_settings()
        self._broker = get_beta_broker_provider()

    async def sync_crypto_universe(self) -> int:
        """Refresh crypto desk symbols from Alpaca (USD pairs, no stables)."""
        items = await resolve_crypto_universe(self._broker)
        set_crypto_symbols(items)
        return len(items)

    def list_desks(self) -> list[dict]:
        return [s.model_dump(mode="json") for s in DESKS.values()]

    async def status(self, desk: AssetDeskId) -> DeskStatus:
        if desk == "crypto":
            try:
                await self.sync_crypto_universe()
            except Exception as exc:
                logger.warning("multiasset.universe_sync_failed", error=str(exc))
        strategy = get_desk(desk)
        configured = self._broker.is_configured()
        dry_flag = bool(getattr(self._settings, "multiasset_autopilot_dry_run", False))
        if configured:
            msg = "Paper Alpaca listo"
            if dry_flag:
                msg += " · OJO: MULTIASSET_AUTOPILOT_DRY_RUN=true → el autopilot NO envía órdenes a Alpaca (solo sim)"
        elif (self._settings.alpaca_beta_api_key or "").strip() and (
            (self._settings.alpaca_beta_api_key or "").strip()
            == (self._settings.alpaca_beta_secret_key or "").strip()
        ):
            msg = (
                "Error de keys: Key ID y Secret deben ser DIFERENTES. "
                "En Alpaca (Paper) → API Keys aparecen dos campos: Key ID (PK…) y Secret Key."
            )
        else:
            msg = (
                "Define ALPACA_BETA_API_KEY (Key ID) y ALPACA_BETA_SECRET_KEY (Secret) "
                "de la cuenta paper — son dos valores distintos"
            )
        equity = cash = None
        positions: list[dict] = []
        orders: list[dict] = []
        quotes: dict = {}
        if configured:
            try:
                acct = await self._broker.get_account()
                equity = float(acct.get("equity") or 0)
                cash = float(acct.get("cash") or 0)
            except Exception as exc:
                msg = f"Cuenta: {exc}"
            try:
                raw_pos = await self._broker.get_positions()
                wanted = desk_symbols(desk)
                for p in raw_pos:
                    if any(same_symbol(str(p.get("symbol") or ""), w) for w in wanted):
                        positions.append(p)
            except Exception as exc:
                logger.warning("multiasset.positions_failed", desk=desk, error=str(exc))
            try:
                orders = await self._broker.list_orders(status="open", limit=40)
                wanted = desk_symbols(desk)
                orders = [
                    o
                    for o in orders
                    if any(same_symbol(str(o.get("symbol") or ""), w) for w in wanted)
                ]
            except Exception:
                orders = []
            for item in strategy.symbols:
                quotes[item.symbol] = await quote_symbol(item.symbol)

        return DeskStatus(
            desk=desk,
            strategy=strategy,
            paper=True,
            broker_configured=configured,
            broker_message=msg,
            equity=equity,
            cash=cash,
            positions=positions,
            open_orders=orders,
            quotes=quotes,
        )

    async def brief(self, desk: AssetDeskId, symbol: str) -> DeskBrief:
        strategy = get_desk(desk)
        sym = normalize_symbol(symbol)
        allowed = {s.symbol.upper() for s in strategy.symbols}
        # allow BTCUSD form for crypto
        allowed |= {s.replace("/", "") for s in allowed}
        if sym not in allowed and sym.replace("/", "") not in allowed:
            # map BTCUSD → BTC/USD if in universe
            for s in strategy.symbols:
                if s.symbol.replace("/", "") == sym.replace("/", ""):
                    sym = s.symbol
                    break
            else:
                raise ValueError(f"{sym} no está en el universo de la mesa {desk}")

        agents = build_agents(strategy.agent_names)
        reports = []
        for agent in agents:
            try:
                reports.append(await agent.analyze(sym))
            except Exception as exc:
                logger.warning("multiasset.agent_failed", agent=agent.name, error=str(exc))

        if not reports:
            raise RuntimeError("Ningún agente respondió")

        # Specialists lead; supporting agents are evidence, not veto except hard risk.
        _WEIGHTS = {
            "gold": {
                "gold_trend_specialist": 3.2,
                "gold_macro_agent": 1.0,
                "gold_technical_agent": 0.8,
                "gold_flow_agent": 0.6,
            },
            "forex": {
                "fx_momentum_specialist": 3.0,
                "fx_macro_agent": 1.0,
                "fx_technical_agent": 0.8,
                "fx_risk_agent": 0.5,
            },
            "crypto": {
                "crypto_breakout_specialist": 3.0,
                "crypto_chart_technical_agent": 1.4,
                "crypto_news_social_agent": 1.0,
                "crypto_momentum_agent": 0.7,
                "crypto_sentiment_agent": 0.6,
                "crypto_risk_agent": 0.4,
            },
        }

        def _w(r) -> float:
            table = _WEIGHTS.get(desk) or {}
            return max(r.confidence, 0.2) * float(table.get(r.agent_name, 1.0))

        num = sum(r.score * _w(r) for r in reports)
        den = sum(_w(r) for r in reports) or 1.0
        score = num / den
        confidence = sum(r.confidence for r in reports) / len(reports)

        spec = next((r for r in reports if (r.raw_data or {}).get("specialist")), None)
        chart = next((r for r in reports if r.agent_name == "crypto_chart_technical_agent"), None)
        news = next((r for r in reports if r.agent_name == "crypto_news_social_agent"), None)

        buy_bar = 8.0 if desk == "crypto" else 10.0
        sell_bar = -8.0 if desk == "crypto" else -10.0
        spec_ok = spec is None or spec.score >= 0
        spec_lead = spec is not None and spec.score >= 12
        if spec_lead and score >= buy_bar and spec_ok:
            rec = "buy"
        elif spec is None and score >= buy_bar:
            rec = "buy"
        elif desk == "crypto" and spec_lead and news is not None and news.score >= 8 and score >= buy_bar - 2:
            rec = "buy"
        elif score <= sell_bar and (spec is None or spec.score <= -8):
            rec = "sell"
        else:
            rec = "hold"
            if spec is not None and spec.score < 8 and score >= buy_bar:
                rec = "hold"

        q = await quote_symbol(sym)
        px = q.get("current_price")
        stop = target = None
        stop_pct = None
        trail = None
        atr_v = None
        if spec and spec.raw_data:
            stop_pct = spec.raw_data.get("stop_pct")
            trail = spec.raw_data.get("trail_atr_mult")
            atr_v = spec.raw_data.get("atr")
            if spec.raw_data.get("stop_px"):
                stop = float(spec.raw_data["stop_px"])
        if px and rec == "buy":
            if stop is None:
                pct = float(stop_pct or strategy.default_stop_pct)
                stop = round(float(px) * (1 - pct), 4)
            target = round(float(px) * (1 + 2 * float(stop_pct or strategy.default_stop_pct)), 4)  # 2R hint; trail can exceed
        elif px and rec == "sell":
            stop = round(float(px) * (1 + strategy.default_stop_pct), 4)
            target = round(float(px) * (1 - strategy.default_target_pct), 4)

        votes = [
            AgentVote(
                agent_name=r.agent_name,
                label_es=_label(r.agent_name),
                score=r.score,
                confidence=r.confidence,
                summary=r.summary,
                risks=[f.statement for f in (r.risks or [])][:3],
                opportunities=[f.statement for f in (r.opportunities or [])][:3],
            )
            for r in reports
        ]
        summary = (
            f"Mesa {desk} · {sym}: score {score:.1f} → {rec.upper()} "
            f"(conf {confidence:.0%}). " + " | ".join(v.summary[:80] for v in votes[:3])
        )
        return DeskBrief(
            desk=desk,
            symbol=sym,
            recommendation=rec,
            confidence=round(confidence, 3),
            score=round(score, 2),
            summary=summary,
            entry_hint=float(px) if px else None,
            stop_hint=stop,
            target_hint=target,
            stop_pct=float(stop_pct) if stop_pct else strategy.default_stop_pct,
            stop_r=1.0,
            trail_atr_mult=float(trail) if trail else None,
            atr=float(atr_v) if atr_v else None,
            leverage=1.0,
            specialist=spec.agent_name if spec else None,
            votes=votes,
            strategy=strategy,
        )

    async def execute(self, req: MultiAssetOrderRequest) -> MultiAssetOrderResult:
        if not self._settings.multiasset_beta_enabled:
            raise ValueError("Módulo multi-asset beta desactivado")
        from services.multiasset.paper_broker import (
            MultiAssetNotPaperError,
            assert_beta_account_is_paper,
        )

        try:
            await assert_beta_account_is_paper(self._broker)
        except MultiAssetNotPaperError as exc:
            raise ValueError(str(exc)) from exc
        strategy = get_desk(req.desk)
        sym = normalize_symbol(req.symbol)
        # canonicalize to universe symbol
        for s in strategy.symbols:
            if s.symbol.replace("/", "") == sym.replace("/", ""):
                sym = s.symbol
                break
        else:
            raise ValueError(f"Símbolo fuera de universo {req.desk}: {req.symbol}")

        if not req.confirm and not req.dry_run:
            raise ValueError("confirm=true requerido para enviar orden paper")

        max_notional = min(
            float(strategy.max_notional_usd),
            float(
                (self._settings.multiasset_crypto_max_notional if req.desk == "crypto" else None)
                or self._settings.multiasset_beta_max_notional
                or 500
            ),
        )
        qty = req.qty
        notional = req.notional
        if qty is None and notional is None:
            raise ValueError("Indica qty o notional")
        if notional is not None and float(notional) > max_notional:
            raise ValueError(f"Notional máx. paper mesa: ${max_notional:.0f}")

        is_crypto = req.desk == "crypto"
        order: dict = {
            "symbol": sym,
            "side": req.side,
            "type": "market",
            "time_in_force": "gtc" if is_crypto or req.side == "buy" else strategy.time_in_force,
        }
        if notional is not None and (is_crypto or strategy.allow_fractional):
            order["notional"] = str(round(float(notional), 2))
        elif qty is not None:
            order["qty"] = str(qty)
        else:
            raise ValueError("Para equity ETF usa qty; crypto puede usar notional")

        # Alpaca crypto has no bracket. Stops are software-only (evaluated each cycle).
        if req.side == "buy" and not req.dry_run and not is_crypto:
            try:
                qstop = await quote_symbol(sym)
                px_s = float((qstop or {}).get("current_price") or 0)
            except Exception:
                px_s = 0.0
            if px_s > 0:
                stop_px = round(px_s * (1 - float(strategy.default_stop_pct)), 8)
                if stop_px <= 0:
                    stop_px = px_s * (1 - float(strategy.default_stop_pct))
                order["order_class"] = "oto"
                order["stop_loss"] = {"stop_price": str(stop_px)}
                order["time_in_force"] = "gtc"

        if req.dry_run or not self._broker.is_configured():
            result = MultiAssetOrderResult(
                ok=True,
                desk=req.desk,
                symbol=sym,
                side=req.side,
                paper=True,
                dry_run=True,
                order_id=None,
                status="dry_run",
                message="Dry-run — orden no enviada" if req.dry_run else "Broker beta no configurado",
                payload={"order": order},
            )
            if self._session is not None and req.dry_run:
                await self._journal_write(req, result)
                await self._track_fill(req, result, sym, is_sim=True)
            return result

        try:
            raw = await self._broker.submit_order(order)
        except Exception as exc:
            if "stop_loss" in order:
                logger.warning("multiasset.oto_failed_retry_plain", error=str(exc), symbol=sym)
                plain = {k: v for k, v in order.items() if k not in {"order_class", "stop_loss"}}
                raw = await self._broker.submit_order(plain)
            else:
                raise
        if isinstance(raw, dict) and str(raw.get("status") or "") in {"rejected", "canceled"} and "stop_loss" in order:
            logger.warning("multiasset.oto_rejected_retry_plain", symbol=sym)
            plain = {k: v for k, v in order.items() if k not in {"order_class", "stop_loss"}}
            raw = await self._broker.submit_order(plain)
        result = MultiAssetOrderResult(
            ok=True,
            desk=req.desk,
            symbol=sym,
            side=req.side,
            paper=True,
            dry_run=False,
            order_id=str(raw.get("id") or "") or None,
            status=str(raw.get("status") or ""),
            message=f"Orden paper enviada ({req.side} {sym})",
            payload=raw if isinstance(raw, dict) else {"raw": raw},
        )
        if self._session is not None:
            await self._journal_write(req, result)
            await self._track_fill(req, result, sym, is_sim=False)
        return result

    async def _track_fill(
        self,
        req: MultiAssetOrderRequest,
        result: MultiAssetOrderResult,
        sym: str,
        *,
        is_sim: bool,
    ) -> None:
        """Open/close tracked trades + attach specialized brief scores for feedback."""
        assert self._session is not None
        tracker = MultiAssetTradeTracker(self._session)
        try:
            q = await quote_symbol(sym)
            px = float(q.get("current_price") or 0) or None
        except Exception:
            px = None
        if not px or px <= 0:
            logger.warning("multiasset.track_no_price", symbol=sym)
            return

        qty = req.qty
        if qty is None and req.notional is not None:
            qty = float(req.notional) / px
        if qty is None or float(qty) <= 0:
            return

        brief: DeskBrief | None = None
        try:
            brief = await self.brief(req.desk, sym)
        except Exception as exc:
            logger.warning("multiasset.track_brief_failed", error=str(exc))

        if req.side == "buy":
            trade = await tracker.open_trade(
                desk=req.desk,
                symbol=sym,
                qty=float(qty),
                entry_price=px,
                brief=brief,
                is_sim=is_sim,
                order_id=result.order_id,
                meta={"note": req.note, "dry_run": is_sim},
            )
            result.payload = {
                **(result.payload or {}),
                "tracked_trade_id": trade.id,
                "entry_price": px,
                "recommendation": trade.recommendation,
            }
            result.message = f"{result.message} · trade abierto @ {px}"
        else:
            closed = await tracker.close_trade(
                desk=req.desk,
                symbol=sym,
                exit_price=px,
                exit_reason=req.note or ("dry-run sell" if is_sim else "paper sell"),
            )
            if closed:
                result.payload = {
                    **(result.payload or {}),
                    "tracked_trade_id": closed.id,
                    "exit_price": px,
                    "pnl_pct": closed.pnl_pct,
                    "was_correct": closed.was_correct,
                    "error_tag": closed.error_tag,
                }
                tag = closed.error_tag or ""
                result.message = (
                    f"{result.message} · cerrado PnL {closed.pnl_pct:+.2f}%"
                    if closed.pnl_pct is not None
                    else result.message
                )
                if tag:
                    result.message += f" · {tag}"
            else:
                result.message = f"{result.message} · sin trade abierto que cerrar"

    async def journal_stop_adjust(
        self,
        *,
        desk: AssetDeskId,
        symbol: str,
        old_stop: float | None,
        new_stop: float,
        reason: str,
    ) -> None:
        """Record trailing / stop-replace in the paper journal (not LIVE)."""
        if self._session is None:
            return
        from domain.multiasset import MultiAssetOrderRequest, MultiAssetOrderResult

        dummy = MultiAssetOrderRequest(desk=desk, symbol=symbol, side="sell", dry_run=True, note=reason)
        result = MultiAssetOrderResult(
            ok=True,
            desk=desk,
            symbol=symbol,
            side="sell",
            paper=True,
            dry_run=True,
            status="stop_adjust",
            message=reason,
            payload={"old_stop": old_stop, "new_stop": new_stop, "kind": "trailing"},
        )
        await self._journal_write(dummy, result)

    async def _journal_write(self, req: MultiAssetOrderRequest, result: MultiAssetOrderResult) -> None:
        assert self._session is not None
        row = MultiAssetJournalORM(
            id=str(uuid4()),
            desk=req.desk,
            symbol=result.symbol,
            side=req.side,
            qty=req.qty,
            notional=req.notional,
            order_id=result.order_id,
            status=result.status,
            note=req.note or result.message,
            created_at=utc_now(),
            raw_json=json.dumps(result.payload, default=str),
        )
        self._session.add(row)
        await self._session.commit()

    async def history(self, desk: AssetDeskId | None = None, limit: int = 40) -> list[MultiAssetJournalEntry]:
        if self._session is None:
            return []
        q = select(MultiAssetJournalORM).order_by(MultiAssetJournalORM.created_at.desc()).limit(limit)
        if desk:
            q = q.where(MultiAssetJournalORM.desk == desk)
        rows = (await self._session.execute(q)).scalars().all()
        out: list[MultiAssetJournalEntry] = []
        for r in rows:
            try:
                raw = json.loads(r.raw_json or "{}")
            except json.JSONDecodeError:
                raw = {}
            out.append(
                MultiAssetJournalEntry(
                    id=r.id,
                    desk=r.desk,  # type: ignore[arg-type]
                    symbol=r.symbol,
                    side=r.side,
                    qty=r.qty,
                    notional=r.notional,
                    order_id=r.order_id,
                    status=r.status,
                    note=r.note or "",
                    created_at=r.created_at,
                    raw=raw,
                )
            )
        return out
