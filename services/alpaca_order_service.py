"""Execute stock orders via Alpaca Trading API.

Patterns aligned with https://github.com/alpacahq/cli:
- client_order_id on every submit (idempotent retries)
- clock / doctor diagnostics
- cancel-all / close-position ops
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from config.settings import get_settings
from domain.broker import (
    BrokerAccount,
    BrokerClock,
    BrokerDoctorReport,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
    BrokerStatus,
    ExecuteLine,
    ExecuteOrdersRequest,
    ExecuteOrdersResponse,
)
from providers.broker.alpaca_provider import AlpacaBrokerProvider
from providers.broker.factory import get_broker_provider
from services.macro_regime_service import MacroRegimeService
from services.risk_policy_service import RiskPolicyService
from utils.logging import get_logger

logger = get_logger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class AlpacaOrderService:
    """Status, account, and order submission against Alpaca."""

    def __init__(
        self,
        broker: AlpacaBrokerProvider | None = None,
        risk_service: RiskPolicyService | None = None,
    ) -> None:
        self._broker = broker or get_broker_provider()
        self._risk = risk_service or RiskPolicyService()
        self._macro = MacroRegimeService()

    @property
    def paper(self) -> bool:
        return self._broker.paper

    def is_configured(self) -> bool:
        return self._broker.is_configured()

    async def status(self) -> BrokerStatus:
        from services.live_safety import production_trading_unconfigured

        if production_trading_unconfigured():
            return BrokerStatus(
                configured=False,
                paper=True,
                connected=False,
                message=(
                    "trading_mode_unconfigured: APP_ENV=production sin ALPACA_PAPER "
                    "ni ALPACA_LIVE_TRADE. Solo lectura; no se conecta a Alpaca."
                ),
                base_url="",
            )
        if not self._broker.is_configured():
            return BrokerStatus(
                configured=False,
                paper=self._broker.paper,
                connected=False,
                message=(
                    "Alpaca no configurada. Define ALPACA_API_KEY + ALPACA_SECRET_KEY "
                    "(compatible con alpacahq/cli). LIVE: ALPACA_PAPER=false o ALPACA_LIVE_TRADE=true."
                ),
                base_url=self._broker.base_url,
            )
        try:
            raw = await self._broker.get_account()
            account = self._map_account(raw)
            clock = None
            market_open = None
            try:
                clock = await self.get_clock()
                market_open = clock.is_open
            except Exception:
                pass
            mode = "Paper" if self._broker.paper else "LIVE"
            open_txt = (
                " · mercado abierto"
                if market_open
                else (" · mercado cerrado" if market_open is False else "")
            )
            return BrokerStatus(
                configured=True,
                paper=self._broker.paper,
                connected=True,
                message=(
                    f"Conectado a Alpaca {mode} · cash ${account.cash:.2f} · "
                    f"equity ${account.equity:.2f}{open_txt}"
                ),
                account=account,
                base_url=self._broker.base_url,
                last_request_id=raw.get("_request_id") or self._broker.last_request_id,
                clock=clock,
                market_open=market_open,
            )
        except Exception as exc:
            return BrokerStatus(
                configured=True,
                paper=self._broker.paper,
                connected=False,
                message=f"Error conectando a Alpaca: {exc}",
                base_url=self._broker.base_url,
                last_request_id=self._broker.last_request_id,
            )

    async def doctor(self) -> BrokerDoctorReport:
        """Connectivity check inspired by `alpaca doctor`."""
        settings = get_settings()
        report = BrokerDoctorReport(
            paper=self._broker.paper,
            configured=self._broker.is_configured(),
            base_url=self._broker.base_url,
            data_base_url=settings.alpaca_data_base_url or "https://data.alpaca.markets",
        )
        if not report.configured:
            report.warnings.append("Faltan ALPACA_API_KEY / ALPACA_SECRET_KEY")
            report.checks.append("credentials: missing")
            return report

        report.checks.append("credentials: present")
        try:
            account = await self.get_account()
            report.trading_reachable = True
            report.account_status = account.status
            report.cash = account.cash
            report.equity = account.equity
            report.checks.append(f"trading account: {account.status}")
            report.last_request_id = self._broker.last_request_id
            if account.trading_blocked or account.account_blocked:
                report.warnings.append("Cuenta bloqueada para trading")
        except Exception as exc:
            report.checks.append(f"trading account: FAIL ({exc})")
            report.warnings.append(str(exc))
            return report

        try:
            clock = await self.get_clock()
            report.market_open = clock.is_open
            report.checks.append(f"clock: {'open' if clock.is_open else 'closed'}")
        except Exception as exc:
            report.checks.append(f"clock: FAIL ({exc})")
            report.warnings.append(str(exc))

        try:
            from providers.market.alpaca_provider import AlpacaMarketDataProvider

            data = AlpacaMarketDataProvider()
            quote = await data.get_quote("AAPL")
            report.data_reachable = quote.get("current_price") is not None
            report.checks.append(
                f"market data: ok (AAPL=${quote.get('current_price')}, "
                f"feed={settings.alpaca_data_feed})"
            )
            report.last_request_id = data.last_request_id or report.last_request_id
        except Exception as exc:
            report.data_reachable = False
            report.checks.append(f"market data: FAIL ({exc})")
            report.warnings.append(str(exc))

        if not self._broker.paper:
            report.warnings.append("Modo LIVE — las órdenes usan dinero real")

        report.ok = report.trading_reachable
        return report

    async def get_clock(self) -> BrokerClock:
        raw = await self._broker.get_clock()
        return BrokerClock(
            is_open=bool(raw.get("is_open")),
            timestamp=_parse_dt(raw.get("timestamp")),
            next_open=_parse_dt(raw.get("next_open")),
            next_close=_parse_dt(raw.get("next_close")),
            raw={k: v for k, v in raw.items() if not str(k).startswith("_")},
        )

    async def get_account(self) -> BrokerAccount:
        raw = await self._broker.get_account()
        return self._map_account(raw)

    async def get_positions(self) -> list[BrokerPosition]:
        raw_list = await self._broker.get_positions()
        return [self._map_position(p) for p in raw_list]

    async def list_orders(
        self, status: str = "all", limit: int = 50, page_token: str | None = None
    ) -> list[BrokerOrderResult]:
        kwargs: dict[str, Any] = {"status": status, "limit": limit}
        if page_token:
            kwargs["page_token"] = page_token
        try:
            raw_list = await self._broker.list_orders(**kwargs)
        except TypeError:
            raw_list = await self._broker.list_orders(status=status, limit=limit)
        return [self._map_order(o) for o in raw_list]

    async def get_order_by_client_order_id(self, client_order_id: str) -> BrokerOrderResult | None:
        getter = getattr(self._broker, "get_order_by_client_order_id", None)
        if getter is None:
            return None
        raw = await getter(client_order_id)
        if not raw:
            return None
        return self._map_order(raw)

    async def find_working_stop(self, symbol: str) -> BrokerOrderResult | None:
        """Live protective stop, including bracket 'held'. Never uses status=open."""
        from services.order_idempotency import order_is_live_stop

        sym = (symbol or "").upper().strip()
        token: str | None = None
        pages = 0
        max_pages = 8
        try:
            while pages < max_pages:
                orders = await self.list_orders(status="all", limit=200, page_token=token)
                for od in orders:
                    if (od.symbol or "").upper() != sym:
                        continue
                    if order_is_live_stop(od):
                        return od
                pages += 1
                token = getattr(self._broker, "last_next_page_token", None) or None
                if not token:
                    break
        except Exception as exc:
            logger.warning("broker.stop_list_all_failed", symbol=sym, error=str(exc))
            raise
        return None

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._broker.cancel_order(order_id)

    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        return await self._broker.cancel_all_orders()

    async def close_position(self, symbol: str) -> dict[str, Any]:
        from services.live_safety import production_trading_unconfigured

        if production_trading_unconfigured():
            raise RuntimeError("trading_mode_unconfigured")
        try:
            return await self._broker.close_position(symbol)
        except Exception as exc:
            logger.warning(
                "broker.close_position_failed_stop_intact",
                symbol=(symbol or "").upper(),
                error=str(exc),
            )
            raise

    async def replace_protective_stop(
        self,
        *,
        symbol: str,
        qty: float,
        stop_price: float,
    ) -> BrokerOrderResult | None:
        """Tighten the existing GTC/bracket stop via PATCH. Never submit a second sell."""
        from services.live_safety import production_trading_unconfigured
        from utils.market_hours import eod_may_submit_orders

        sym = symbol.upper().strip()
        if qty <= 0 or stop_price <= 0:
            return None
        if production_trading_unconfigured():
            logger.warning("broker.stop_replace_blocked", symbol=sym, reason="trading_mode_unconfigured")
            return BrokerOrderResult(
                symbol=sym,
                qty=float(qty),
                side="sell",
                type="stop",
                status="failed",
                error="trading_mode_unconfigured",
            )
        if not eod_may_submit_orders():
            logger.warning("broker.stop_replace_blocked_after_close", symbol=sym)
            return BrokerOrderResult(
                symbol=sym,
                qty=float(qty),
                side="sell",
                type="stop",
                status="failed",
                error="after_regular_close_no_orders",
            )
        try:
            existing = await self.find_working_stop(sym)
        except Exception as exc:
            logger.warning("broker.stop_list_orders_failed", symbol=sym, error=str(exc))
            return BrokerOrderResult(
                symbol=sym,
                qty=float(qty),
                side="sell",
                type="stop",
                status="failed",
                error="list_orders_failed_stop_intact",
            )
        new_stop = round(float(stop_price), 2)
        if existing and existing.id:
            replace = getattr(self._broker, "replace_order", None)
            if replace is None:
                logger.warning(
                    "broker.stop_patch_unavailable",
                    symbol=sym,
                    order_id=existing.id,
                )
                return BrokerOrderResult(
                    id=existing.id,
                    symbol=sym,
                    qty=float(qty),
                    side="sell",
                    type=existing.type or "stop",
                    status="failed",
                    error="replace_order_unavailable",
                    raw=existing.raw or {},
                )
            try:
                raw = await replace(existing.id, stop_price=new_stop)
                mapped = self._map_order(raw if isinstance(raw, dict) else {})
                logger.info(
                    "broker.stop_patched",
                    symbol=sym,
                    order_id=existing.id,
                    stop=new_stop,
                )
                return mapped
            except Exception as exc:
                logger.warning(
                    "broker.stop_patch_failed",
                    symbol=sym,
                    order_id=existing.id,
                    error=str(exc),
                )
                return BrokerOrderResult(
                    id=existing.id,
                    symbol=sym,
                    qty=float(existing.qty or qty),
                    side="sell",
                    type=existing.type or "stop",
                    status="failed",
                    error=f"patch_failed:{exc}",
                    raw=existing.raw or {},
                )
        # No working stop — place a standalone GTC (do not cancel anything).
        settings = get_settings()
        allow_overnight_carry = bool(
            settings.intraday_only_enabled and settings.intraday_flat_winners_only
        )
        tif = "gtc" if (not settings.intraday_only_enabled or allow_overnight_carry) else "day"
        return await self._place_standalone_stop(
            symbol=sym, qty=float(qty), stop_price=new_stop, tif=tif
        )

    async def _allocate_stop_client_order_id(self, symbol: str) -> str:
        """Persisted attempt, or UUID if the counter is unavailable. Never a daily -1."""
        from uuid import uuid4

        from services.live_cycle_lock import allocate_live_client_order_id
        from services.order_idempotency import AttemptUnavailable

        try:
            from database.engine import get_session
            from database.repositories.ops_repository import OpsFlagRepository

            async for session in get_session():
                flags = OpsFlagRepository(session)
                cid, _ = await allocate_live_client_order_id(
                    flags, symbol, "stop", broker=self
                )
                return cid
        except AttemptUnavailable:
            logger.warning("broker.stop_attempt_unavailable_uuid", symbol=symbol)
        except Exception as exc:
            logger.warning("broker.stop_allocate_fallback_uuid", symbol=symbol, error=str(exc))
        return f"stop-{uuid4().hex[:12]}"

    async def _place_standalone_stop(
        self,
        *,
        symbol: str,
        qty: float,
        stop_price: float,
        tif: str,
        retry: bool = True,
    ) -> BrokerOrderResult:
        from services.desk_ops_alert import (
            KIND_STOP_NOT_LIVE,
            KIND_STOP_RECHECK_EMPTY,
            emit_desk_ops_alert,
        )
        from services.order_idempotency import (
            is_insufficient_qty_error,
            is_stale_stop_status,
            order_is_live_stop,
        )

        cid = await self._allocate_stop_client_order_id(symbol)
        placed = await self.submit_one(
            BrokerOrderRequest(
                symbol=symbol,
                qty=float(qty),
                side="sell",
                order_type="stop",
                time_in_force=tif,
                stop_price=stop_price,
                client_order_id=cid,
                source_tag="desk",
            )
        )
        if placed and not placed.error and order_is_live_stop(placed):
            return placed

        err = (placed.error if placed else None) or ""
        status = str((placed.status if placed else "") or "").lower()
        stale = bool(
            (placed.raw or {}).get("not_live_stop")
            or err.startswith("stale_order:")
            or err.startswith("stop_not_live:")
            or is_stale_stop_status(status)
        )
        if retry and placed and stale:
            try:
                from database.engine import get_session
                from database.repositories.ops_repository import OpsFlagRepository
                from services.order_idempotency import (
                    AttemptUnavailable,
                    attempt_slot,
                    bump_attempt,
                    cycle_key_date,
                )

                async for session in get_session():
                    flags = OpsFlagRepository(session)
                    await bump_attempt(flags, attempt_slot(symbol, "stop", cycle_key_date()))
                    break
            except AttemptUnavailable:
                logger.warning("broker.stop_bump_unavailable", symbol=symbol)
            except Exception as exc:
                logger.warning("broker.stop_bump_failed", symbol=symbol, error=str(exc))
            return await self._place_standalone_stop(
                symbol=symbol, qty=qty, stop_price=stop_price, tif=tif, retry=False
            )

        if placed and is_insufficient_qty_error(placed.error):
            try:
                again = await self.find_working_stop(symbol)
            except Exception:
                again = None
            if again:
                logger.info(
                    "broker.stop_insufficient_qty_still_protected",
                    symbol=symbol,
                    order_id=again.id,
                    status=again.status,
                )
                raw = dict(again.raw or {})
                raw["rechecked_after_insufficient_qty"] = True
                return again.model_copy(update={"raw": raw, "error": None})
            logger.warning("broker.stop_insufficient_qty_recheck_empty", symbol=symbol)
            await emit_desk_ops_alert(
                KIND_STOP_RECHECK_EMPTY,
                detail=f"{symbol} second find_working_stop empty after insufficient qty",
            )
            return BrokerOrderResult(
                symbol=symbol,
                qty=float(qty),
                side="sell",
                type="stop",
                status="failed",
                error="insufficient_qty_stop_recheck_empty",
            )

        if placed and (placed.error or not order_is_live_stop(placed)):
            not_live = status or (placed.error or "unknown")
            await emit_desk_ops_alert(
                KIND_STOP_NOT_LIVE,
                detail=f"{symbol} status={not_live} id={getattr(placed, 'id', '')} cid={cid}",
            )
            extra = dict(placed.raw or {})
            extra["not_live_stop"] = True
            return placed.model_copy(
                update={
                    "raw": extra,
                    "status": "failed",
                    "error": placed.error or f"stop_not_live:{status}",
                }
            )
        if placed is None:
            return BrokerOrderResult(
                symbol=symbol,
                qty=float(qty),
                side="sell",
                type="stop",
                status="failed",
                error="stop_place_empty",
            )
        return placed

    async def latest_filled_sell(self, symbol: str) -> BrokerOrderResult | None:
        """Most recent filled sell for journal/cooldown (broker stop or close)."""
        sym = (symbol or "").upper().strip()
        try:
            closed = await self.list_orders(status="closed", limit=50)
        except Exception:
            return None
        for od in closed:
            if (od.symbol or "").upper() != sym:
                continue
            if (od.side or "").lower() != "sell":
                continue
            from services.live_safety import filled_exit_price_from_order

            if filled_exit_price_from_order(od):
                return od
        return None

    async def close_all_positions(self, *, cancel_orders: bool = True) -> list[dict[str, Any]]:
        return await self._broker.close_all_positions(cancel_orders=cancel_orders)

    async def _live_buy_central_gates(
        self, settings: Any, *, skip_daily_cap: bool = False
    ) -> str | None:
        """Same LIVE buy filters as execute(): kill, deposited brake, daily cap, cooldown.

        ``skip_daily_cap`` is for the autopilot reservation already consumed this cycle.
        Returns an error tag or None if the buy may proceed. Fail-closed.
        """
        from services.live_safety import FLAG_ENTRY_DAY, remaining_entry_slots

        try:
            from database.engine import get_session
            from services.kill_switch_service import KillSwitchService

            kill_active = False
            checked = False
            async for session in get_session():
                kill_active = await KillSwitchService(session, self).is_active()
                flags = None
                try:
                    from database.repositories.ops_repository import OpsFlagRepository

                    flags = OpsFlagRepository(session)
                    cool = await flags.get_json("post_stop_cooldown")
                    until = float((cool or {}).get("until") or 0)
                    if until and __import__("time").time() < until:
                        return "post_stop_cooldown"
                    day_flag = await flags.get_json(FLAG_ENTRY_DAY)
                    if not skip_daily_cap and remaining_entry_slots(
                        day_flag,
                        max_entries=int(getattr(settings, "live_max_entries_per_day", 1) or 1),
                    ) <= 0:
                        return "max_1_entry_per_day"
                except Exception:
                    return "entry_gate_read_failed"
                checked = True
                break
            if not checked:
                return "kill_switch_check_failed"
            if kill_active:
                return "kill_switch_entries_blocked"
        except Exception:
            return "kill_switch_check_failed"
        try:
            from services.deposited_capital_service import resolve_trading_base
            from services.live_safety import deposited_brake_triggered

            acct = await self.get_account()
            eq = float(acct.equity or 0)
            snap = await resolve_trading_base(equity=eq)
            base = snap.amount if snap.amount and snap.amount > 0 else None
            if base is None:
                return "deposited_base_missing"
            pct = float(getattr(settings, "deposited_brake_pct", 5.0) or 5.0)
            if deposited_brake_triggered(eq, base, pct):
                return "deposited_brake"
        except Exception:
            return "deposited_brake_check_failed"
        return None

    def _reconcile_existing_order(
        self, req: BrokerOrderRequest, raw: dict[str, Any] | BrokerOrderResult
    ) -> BrokerOrderResult:
        """Map an already-accepted broker order. Never treat dead/mismatch as a fill."""
        from services.order_idempotency import (
            is_dead_retryable_status,
            is_protective_stop_request,
            order_is_live_stop,
        )

        if isinstance(raw, BrokerOrderResult):
            mapped = raw
            extra = dict(mapped.raw or {})
        else:
            mapped = self._map_order(raw)
            extra = dict(mapped.raw or {})
        extra["reconciled"] = True
        extra["no_new_fill"] = True
        status = str(mapped.status or "").lower()
        if is_dead_retryable_status(status):
            extra["stale"] = True
            extra["not_live_stop"] = True if is_protective_stop_request(req) else extra.get(
                "not_live_stop"
            )
            return mapped.model_copy(
                update={"raw": extra, "status": "failed", "error": f"stale_order:{status}"}
            )
        if is_protective_stop_request(req) and not order_is_live_stop(mapped):
            extra["stale"] = True
            extra["not_live_stop"] = True
            return mapped.model_copy(
                update={"raw": extra, "status": "failed", "error": f"stale_order:{status}"}
            )
        if (mapped.symbol or "").upper() != (req.symbol or "").upper():
            return mapped.model_copy(
                update={"raw": extra, "status": "failed", "error": "reconcile_symbol_mismatch"}
            )
        if (mapped.side or "").lower() != (req.side or "").lower():
            return mapped.model_copy(
                update={"raw": extra, "status": "failed", "error": "reconcile_side_mismatch"}
            )
        try:
            want = float(req.qty or 0)
            have = float(mapped.qty or 0)
        except (TypeError, ValueError):
            want, have = 0.0, -1.0
        if want > 0 and have > 0 and abs(want - have) > 1e-6:
            return mapped.model_copy(
                update={"raw": extra, "status": "failed", "error": "reconcile_qty_mismatch"}
            )
        return mapped.model_copy(update={"raw": extra, "error": None})

    async def _alert_order_uncertain(self, symbol: str, client_id: str) -> None:
        try:
            from services.desk_ops_alert import KIND_ORDER_UNCERTAIN, emit_desk_ops_alert

            await emit_desk_ops_alert(
                KIND_ORDER_UNCERTAIN,
                detail=f"{symbol} client_order_id={client_id}",
            )
        except Exception as exc:
            logger.warning("broker.uncertain_alert_failed", symbol=symbol, error=str(exc))

    async def _lookup_by_client_order_id(self, client_id: str) -> dict[str, Any]:
        """404 = missing (retry same id). 5xx/network = uncertain (do not POST)."""
        from services.order_idempotency import alpaca_http_status, is_timeout_or_network

        try:
            order = await self.get_order_by_client_order_id(client_id)
        except Exception as exc:
            status = alpaca_http_status(exc)
            if status == 404:
                return {"missing": True, "uncertain": False, "order": None}
            logger.warning(
                "broker.by_client_order_id_uncertain",
                client_order_id=client_id,
                error=str(exc),
            )
            return {
                "missing": False,
                "uncertain": True,
                "order": None,
                "error": str(exc),
                "network": is_timeout_or_network(exc),
            }
        if order is None:
            return {"missing": True, "uncertain": False, "order": None}
        return {"missing": False, "uncertain": False, "order": order}

    async def _submit_payload_idempotent(
        self,
        req: BrokerOrderRequest,
        payload: dict[str, Any],
        failed: BrokerOrderResult,
    ) -> BrokerOrderResult:
        from services.order_idempotency import (
            is_duplicate_client_order_id_error,
            is_timeout_or_network,
            is_unrelated_422,
        )

        client_id = str(payload.get("client_order_id") or "")
        try:
            raw = await self._broker.submit_order(payload)
            return self._map_order(raw)
        except Exception as exc:
            if is_unrelated_422(exc):
                return failed.model_copy(
                    update={
                        "error": f"alpaca_422:{exc}",
                        "client_order_id": client_id,
                        "request_id": self._broker.last_request_id,
                    }
                )
            if is_duplicate_client_order_id_error(exc):
                looked = await self._lookup_by_client_order_id(client_id)
                existing = looked.get("order")
                if existing:
                    logger.info(
                        "broker.duplicate_coid_reconciled",
                        symbol=req.symbol,
                        client_order_id=client_id,
                        order_id=existing.id,
                        status=existing.status,
                    )
                    return self._reconcile_existing_order(req, existing)
                if looked.get("uncertain"):
                    await self._alert_order_uncertain(req.symbol, client_id)
                    return failed.model_copy(
                        update={
                            "error": "order_uncertain_no_retry",
                            "client_order_id": client_id,
                            "request_id": self._broker.last_request_id,
                        }
                    )
                return failed.model_copy(
                    update={
                        "error": "duplicate_client_order_id_unresolved",
                        "client_order_id": client_id,
                        "request_id": self._broker.last_request_id,
                    }
                )
            if is_timeout_or_network(exc):
                looked = await self._lookup_by_client_order_id(client_id)
                existing = looked.get("order")
                if existing:
                    logger.info(
                        "broker.timeout_order_exists_reconciled",
                        symbol=req.symbol,
                        client_order_id=client_id,
                        order_id=existing.id,
                    )
                    return self._reconcile_existing_order(req, existing)
                if looked.get("uncertain"):
                    logger.warning(
                        "broker.timeout_lookup_uncertain_no_post",
                        symbol=req.symbol,
                        client_order_id=client_id,
                    )
                    await self._alert_order_uncertain(req.symbol, client_id)
                    return failed.model_copy(
                        update={
                            "error": "order_uncertain_no_retry",
                            "client_order_id": client_id,
                            "request_id": self._broker.last_request_id,
                        }
                    )
                try:
                    raw = await self._broker.submit_order(payload)
                    return self._map_order(raw)
                except Exception as retry_exc:
                    if is_duplicate_client_order_id_error(retry_exc):
                        again = await self._lookup_by_client_order_id(client_id)
                        if again.get("order"):
                            return self._reconcile_existing_order(req, again["order"])
                    return failed.model_copy(
                        update={
                            "error": str(retry_exc),
                            "client_order_id": client_id,
                            "request_id": self._broker.last_request_id,
                        }
                    )
            return failed.model_copy(
                update={
                    "error": str(exc),
                    "client_order_id": client_id,
                    "request_id": self._broker.last_request_id,
                }
            )

    async def submit_one(
        self, req: BrokerOrderRequest, *, skip_daily_cap: bool = False
    ) -> BrokerOrderResult:
        from services.live_safety import (
            is_buy_side,
            live_entry_blocked,
            long_qty_from_positions,
            production_trading_unconfigured,
            sell_qty_exceeds_long,
        )
        from utils.market_hours import eod_may_submit_orders

        settings = get_settings()
        failed = BrokerOrderResult(
            symbol=req.symbol.upper(),
            qty=req.qty,
            side=req.side,
            type=req.order_type,
            status="failed",
        )
        if production_trading_unconfigured():
            return failed.model_copy(update={"error": "trading_mode_unconfigured"})
        if not is_buy_side(req.side):
            try:
                positions = await self.get_positions()
            except Exception:
                positions = []
            have = long_qty_from_positions(positions, req.symbol)
            if sell_qty_exceeds_long(req.qty, have):
                return failed.model_copy(update={"error": "sell_qty_exceeds_long"})
        blocked, why = live_entry_blocked(
            side=req.side,
            paper=self._broker.paper,
            live_entries_enabled=bool(getattr(settings, "live_entries_enabled", False)),
        )
        if blocked:
            return failed.model_copy(update={"error": why})
        if is_buy_side(req.side) and not eod_may_submit_orders():
            return failed.model_copy(update={"error": "after_regular_close_no_orders"})
        if is_buy_side(req.side) and not self._broker.paper:
            gate_err = await self._live_buy_central_gates(
                settings, skip_daily_cap=bool(skip_daily_cap)
            )
            if gate_err:
                return failed.model_copy(update={"error": gate_err})
        payload = self._build_order_payload(req)
        return await self._submit_payload_idempotent(req, payload, failed)

    async def execute(
        self,
        request: ExecuteOrdersRequest,
        *,
        skip_daily_cap: bool = False,
        allow_entry: Any = None,
    ) -> ExecuteOrdersResponse:
        warnings: list[str] = []
        from services.live_safety import production_trading_unconfigured

        if production_trading_unconfigured():
            return ExecuteOrdersResponse(
                paper=self._broker.paper,
                dry_run=request.dry_run,
                warnings=[
                    "trading_mode_unconfigured: APP_ENV=production sin ALPACA_PAPER "
                    "ni ALPACA_LIVE_TRADE. Trading en solo-lectura; no se elige paper/LIVE."
                ],
            )
        if not self._broker.is_configured():
            return ExecuteOrdersResponse(
                paper=self._broker.paper,
                dry_run=request.dry_run,
                warnings=[
                    "Alpaca no configurada. Añade ALPACA_API_KEY y ALPACA_SECRET_KEY."
                ],
            )

        if not self._broker.paper and not request.confirm_live:
            return ExecuteOrdersResponse(
                paper=False,
                dry_run=request.dry_run,
                warnings=[
                    "Cuenta LIVE detectada. Para enviar órdenes reales envía "
                    "confirm_live=true (o ALPACA_LIVE_TRADE=true + confirmación)."
                ],
            )

        if not self._broker.paper:
            warnings.append("ATENCIÓN: órdenes en cuenta LIVE con dinero real.")

        # --- Kill switch: block entries, allow exits (brackets stay). ---
        kill_active = False
        has_buy = any((ln.side or "").lower() == "buy" for ln in request.lines)
        try:
            from database.engine import get_session
            from services.kill_switch_service import KillSwitchService

            async for session in get_session():
                kill_active = await KillSwitchService(session, self).is_active()
                break
        except Exception as exc:
            warnings.append(f"Kill switch check falló ({exc})")
            if has_buy and not self._broker.paper and not request.dry_run:
                return ExecuteOrdersResponse(
                    paper=self._broker.paper,
                    dry_run=request.dry_run,
                    warnings=["kill_switch_check_failed_fail_closed"],
                )
        if kill_active:
            warnings.append(
                "KILL SWITCH ACTIVO — entradas bloqueadas; stops/TP/salidas permitidos."
            )

        account = None
        positions: list[BrokerPosition] = []
        try:
            account = await self.get_account()
            buy_only = any(ln.side == "buy" for ln in request.lines) and not any(
                ln.side == "sell" for ln in request.lines
            )
            if (
                buy_only
                and account.cash <= 0
                and account.buying_power <= 0
                and not request.dry_run
            ):
                return ExecuteOrdersResponse(
                    paper=self._broker.paper,
                    dry_run=request.dry_run,
                    warnings=[
                        "Tu cuenta Alpaca tiene cash/buying power ≈ $0. "
                        "Fondea en app.alpaca.markets → Fund your account. "
                        "Sin fondos la orden se rechaza y no aparece en el portafolio."
                    ],
                )
        except Exception:
            pass

        try:
            positions = await self.get_positions()
        except Exception:
            positions = []

        # Accumulated 5% vs deposited — arm kill without flatten; allow exits.
        if account and not self._broker.paper and not request.dry_run:
            try:
                from services.deposited_capital_service import get_deposited_base
                from services.live_safety import arm_deposited_brake_if_needed

                eq = float(account.equity or 0)
                base_snap = await get_deposited_base()
                base = base_snap.amount if base_snap.amount and base_snap.amount > 0 else None
                if has_buy and base is None:
                    return ExecuteOrdersResponse(
                        paper=self._broker.paper,
                        dry_run=request.dry_run,
                        warnings=["deposited_base_missing"],
                    )
                pct = float(getattr(get_settings(), "deposited_brake_pct", 5.0) or 5.0)
                from database.engine import get_session as _gsess

                async for session in _gsess():
                    armed = await arm_deposited_brake_if_needed(
                        session,
                        self,
                        equity=eq,
                        base=base,
                        pct=pct,
                        actor="deposited_brake",
                    )
                    if armed:
                        kill_active = True
                        warnings.append(
                            armed.get("reason")
                            or "Freno 5% vs depositado — entradas bloqueadas, brackets intactos."
                        )
                    break
            except Exception as exc:
                warnings.append(f"deposited_brake check falló ({exc})")
                if has_buy and not self._broker.paper and not request.dry_run:
                    return ExecuteOrdersResponse(
                        paper=self._broker.paper,
                        dry_run=request.dry_run,
                        warnings=["deposited_brake_check_failed_fail_closed"],
                    )
        elif has_buy and not self._broker.paper and not request.dry_run:
            return ExecuteOrdersResponse(
                paper=self._broker.paper,
                dry_run=request.dry_run,
                warnings=["deposited_base_missing"],
            )

        # --- Risk desk + macro gate ---
        policy = self._risk.policy_from_settings()
        portfolio_snap = None
        macro = None
        try:
            macro = await self._macro.assess()
            if account:
                portfolio_snap = await self._risk.snapshot_from_account(account, positions)
            if macro.mode in ("risk_off", "crisis"):
                warnings.append(macro.thesis)
            if not macro.trading_allowed:
                buy_lines = [ln for ln in request.lines if ln.side == "buy"]
                if buy_lines and not request.dry_run:
                    return ExecuteOrdersResponse(
                        paper=self._broker.paper,
                        dry_run=request.dry_run,
                        warnings=[
                            macro.block_reason
                            or "Régimen crisis: Risk Desk bloqueó nuevas compras.",
                            *warnings,
                        ],
                        failed=[
                            BrokerOrderResult(
                                symbol=ln.ticker.upper(),
                                qty=ln.shares,
                                side=ln.side,
                                type=ln.order_type,
                                status="failed",
                                error="Bloqueado por Risk Desk (crisis macro).",
                            )
                            for ln in buy_lines
                        ],
                    )
        except Exception as exc:
            warnings.append(f"Risk/macro desk no disponible ({exc}); se continúa con checks básicos.")
            macro = None

        # --- VaR / beta / sector hard gates ---
        risk_metrics = None
        settings = get_settings()
        if account and positions and any(ln.side == "buy" for ln in request.lines):
            try:
                from services.portfolio_risk_metrics_service import PortfolioRiskMetricsService

                equity = float(account.equity or account.portfolio_value or 0.0)
                from services.deposited_capital_service import get_deposited_base

                base_snap = await get_deposited_base()
                capital_base = base_snap.amount if base_snap.amount and base_snap.amount > 0 else None
                risk_metrics = await PortfolioRiskMetricsService().compute(
                    positions,
                    equity=equity,
                    capital_base=capital_base,
                )
                if risk_metrics.warnings:
                    warnings.extend(risk_metrics.warnings[:3])
                # Ultra-micro books (~$50): one liquid name can push historical VaR
                # over the institutional 8% gate while still respecting concentration,
                # cash reserve and max-notional. Soft-warn so the continuous hunt
                # can keep buying within those caps.
                ultra_micro_book = 0 < equity <= 50.0
                if (
                    settings.risk_enforce_var_beta
                    and risk_metrics.var_1d_95_pct is not None
                    and risk_metrics.var_1d_95_pct > settings.risk_max_var_pct
                ):
                    buy_lines = [ln for ln in request.lines if ln.side == "buy"]
                    if buy_lines and not request.dry_run:
                        if ultra_micro_book:
                            warnings.append(
                                f"VaR 1d 95% {risk_metrics.var_1d_95_pct:.1f}% > "
                                f"límite {settings.risk_max_var_pct:.1f}% — aviso "
                                f"(libro ultra-micro ${equity:.0f}; no bloquea; "
                                "rige tope 35%/posición + notional)."
                            )
                        else:
                            return ExecuteOrdersResponse(
                                paper=self._broker.paper,
                                dry_run=request.dry_run,
                                warnings=[
                                    f"VaR 1d 95% {risk_metrics.var_1d_95_pct:.1f}% > "
                                    f"límite {settings.risk_max_var_pct:.1f}% — compras bloqueadas.",
                                    *warnings,
                                ],
                                failed=[
                                    BrokerOrderResult(
                                        symbol=ln.ticker.upper(),
                                        qty=ln.shares,
                                        side=ln.side,
                                        type=ln.order_type,
                                        status="failed",
                                        error="Bloqueado por VaR del portafolio",
                                    )
                                    for ln in buy_lines
                                ],
                            )
                if (
                    settings.risk_enforce_var_beta
                    and risk_metrics.portfolio_beta is not None
                    and risk_metrics.portfolio_beta > settings.risk_max_portfolio_beta
                ):
                    warnings.append(
                        f"Beta portafolio {risk_metrics.portfolio_beta:.2f} > "
                        f"{settings.risk_max_portfolio_beta:.2f} — selectividad alta."
                    )
            except Exception as exc:
                warnings.append(f"VaR/beta metrics falló ({exc})")

        try:
            clock = await self.get_clock()
            if not clock.is_open and not request.dry_run:
                warnings.append(
                    "Mercado cerrado ahora — si Alpaca acepta la orden, búscala en "
                    f"Orders/Activity (pending). next_open={clock.next_open}."
                )
        except Exception:
            pass

        submitted: list[BrokerOrderResult] = []
        failed: list[BrokerOrderResult] = []
        request_ids: list[str] = []

        from services.live_safety import is_buy_side, live_buys_allowed
        from utils.market_hours import eod_may_submit_orders

        for line in request.lines:
            if is_buy_side(line.side) and not request.dry_run:
                if allow_entry is not None:
                    try:
                        lease_ok = await allow_entry()
                    except Exception as exc:
                        logger.warning("broker.entry_lease_check_failed", error=str(exc))
                        lease_ok = False
                    if not lease_ok:
                        failed.append(
                            BrokerOrderResult(
                                symbol=line.ticker.upper(),
                                qty=line.shares,
                                side=line.side,
                                type=line.order_type,
                                status="failed",
                                error="lease_lost_mid_cycle",
                            )
                        )
                        continue
                if not eod_may_submit_orders():
                    failed.append(
                        BrokerOrderResult(
                            symbol=line.ticker.upper(),
                            qty=line.shares,
                            side=line.side,
                            type=line.order_type,
                            status="failed",
                            error="after_regular_close_no_orders",
                        )
                    )
                    continue
                if kill_active:
                    failed.append(
                        BrokerOrderResult(
                            symbol=line.ticker.upper(),
                            qty=line.shares,
                            side=line.side,
                            type=line.order_type,
                            status="failed",
                            error="kill_switch_entries_blocked",
                        )
                    )
                    continue
                ok_buy, buy_why = live_buys_allowed(
                    paper=self._broker.paper,
                    live_entries_enabled=bool(getattr(settings, "live_entries_enabled", False)),
                )
                if ok_buy and not self._broker.paper:
                    from services.deposited_capital_service import get_deposited_base

                    base_gate = await get_deposited_base()
                    if not base_gate.buy_allowed:
                        ok_buy, buy_why = False, "deposited_base_missing"
                if not ok_buy:
                    failed.append(
                        BrokerOrderResult(
                            symbol=line.ticker.upper(),
                            qty=line.shares,
                            side=line.side,
                            type=line.order_type,
                            status="failed",
                            error=buy_why,
                        )
                    )
                    continue

            if account and line.side == "buy":
                if account.buying_power < 0.01 and account.cash < 0.01 and not request.dry_run:
                    failed.append(
                        BrokerOrderResult(
                            symbol=line.ticker.upper(),
                            qty=line.shares,
                            side=line.side,
                            type=line.order_type,
                            status="failed",
                            error="Fondos insuficientes en Alpaca (cash $0). Fondea la cuenta.",
                        )
                    )
                    continue

                # Hard risk policy on buys
                if macro is not None:
                    # Estimate price from stop/TP mid or skip size checks without price
                    est_price = None
                    if line.limit_price:
                        est_price = line.limit_price
                    elif line.stop_loss and line.take_profit:
                        est_price = (line.stop_loss + line.take_profit) / 2
                    stop = line.stop_loss
                    tp = line.take_profit
                    # Risk desk: attach default protective stop if policy requires it
                    if policy.require_stop_loss and (stop is None or stop <= 0) and est_price:
                        stop = round(est_price * 0.92, 4)
                        warnings.append(
                            f"{line.ticker.upper()}: Risk Desk añadió stop -8% @ ${stop}."
                        )
                    if (tp is None or tp <= 0) and est_price and stop:
                        tp = round(est_price * 1.12, 4)
                        warnings.append(
                            f"{line.ticker.upper()}: Risk Desk añadió take-profit +12% @ ${tp}."
                        )
                    verdict = self._risk.evaluate_buy(
                        symbol=line.ticker,
                        qty=line.shares,
                        price=est_price,
                        stop_loss=stop,
                        take_profit=tp,
                        policy=policy,
                        macro_mode=macro.mode,
                        size_multiplier=macro.size_multiplier,
                        portfolio=portfolio_snap,
                        trading_allowed=macro.trading_allowed,
                        block_reason=macro.block_reason,
                    )
                    warnings.extend(verdict.warnings)
                    if not verdict.allowed:
                        failed.append(
                            BrokerOrderResult(
                                symbol=line.ticker.upper(),
                                qty=line.shares,
                                side=line.side,
                                type=line.order_type,
                                status="failed",
                                error="; ".join(verdict.reasons) or "Rechazado por Risk Desk.",
                            )
                        )
                        continue
                    updates: dict[str, Any] = {}
                    if verdict.adjusted_qty is not None and verdict.adjusted_qty + 1e-9 < line.shares:
                        updates["shares"] = verdict.adjusted_qty
                    if stop and stop != line.stop_loss:
                        updates["stop_loss"] = stop
                    if tp and tp != line.take_profit:
                        updates["take_profit"] = tp
                    if updates:
                        line = line.model_copy(update=updates)

                # Sector concentration hard gate
                if settings.risk_enforce_sector_cap and risk_metrics is not None:
                    try:
                        from providers.market.factory import get_market_provider
                        from services.portfolio_risk_metrics_service import PortfolioRiskMetricsService

                        quote = await get_market_provider().get_quote(line.ticker.upper())
                        sector = quote.get("sector") or "Unknown"
                        est = line.limit_price
                        if not est and line.stop_loss and line.take_profit:
                            est = (line.stop_loss + line.take_profit) / 2
                        notional = float(line.shares) * float(est or 0)
                        ok, reasons = PortfolioRiskMetricsService().gate_buy(
                            metrics=risk_metrics,
                            symbol=line.ticker,
                            notional=notional,
                            sector=sector,
                            beta=float(quote["beta"]) if quote.get("beta") is not None else None,
                            max_var_pct=settings.risk_max_var_pct,
                            max_beta=settings.risk_max_portfolio_beta,
                            max_sector_pct=settings.risk_max_sector_pct,
                        )
                        # Only enforce sector here (VaR already gated book-wide)
                        sector_reasons = [r for r in reasons if "Sector" in r]
                        if sector_reasons:
                            failed.append(
                                BrokerOrderResult(
                                    symbol=line.ticker.upper(),
                                    qty=line.shares,
                                    side=line.side,
                                    type=line.order_type,
                                    status="failed",
                                    error="; ".join(sector_reasons),
                                )
                            )
                            continue
                    except Exception as exc:
                        warnings.append(f"{line.ticker}: sector gate skip ({exc})")

            # Bracket TIF: GTC when red names may carry overnight (winners-only EOD).
            # Pure day-flat mode keeps day TIF.
            settings = get_settings()
            want_bracket = bool(
                line.side == "buy"
                and line.stop_loss
                and line.take_profit
                and line.order_type == "market"
            )
            allow_overnight_carry = bool(
                settings.intraday_only_enabled and settings.intraday_flat_winners_only
            )
            use_gtc = want_bracket and (
                not settings.intraday_only_enabled or allow_overnight_carry
            )
            order_req = BrokerOrderRequest(
                symbol=line.ticker.upper().strip(),
                qty=line.shares,
                side=line.side,
                order_type=line.order_type,
                time_in_force="gtc" if use_gtc else "day",
                limit_price=line.limit_price,
                take_profit=line.take_profit,
                stop_loss=line.stop_loss,
                client_order_id=line.client_order_id,
                source_tag=getattr(line, "source_tag", None) or "desk",
            )
            if not request.dry_run:
                try:
                    asset = await self._broker.get_asset(order_req.symbol)
                    tradable = asset.get("tradable", True)
                    status = str(asset.get("status") or "")
                    if not tradable or status.lower() not in ("", "active"):
                        failed.append(
                            BrokerOrderResult(
                                symbol=order_req.symbol,
                                qty=order_req.qty,
                                side=order_req.side,
                                type=order_req.order_type,
                                status="failed",
                                error=(
                                    f"Activo no operable en Alpaca "
                                    f"(tradable={tradable}, status={status or 'n/a'}). "
                                    "Elige otro ticker de la lista US."
                                ),
                            )
                        )
                        continue
                except Exception as exc:
                    # If asset lookup fails, still try the order — Alpaca will reject clearly
                    warnings.append(f"{order_req.symbol}: no se pudo verificar asset ({exc})")

            if request.dry_run:
                payload = self._build_order_payload(order_req)
                submitted.append(
                    BrokerOrderResult(
                        symbol=order_req.symbol,
                        qty=order_req.qty,
                        side=order_req.side,
                        type=order_req.order_type,
                        status="dry_run",
                        client_order_id=str(payload.get("client_order_id") or ""),
                        raw={"payload": payload},
                    )
                )
                continue

            result = await self.submit_one(order_req, skip_daily_cap=bool(skip_daily_cap))
            if result.request_id:
                request_ids.append(result.request_id)
            if result.error or result.status == "failed":
                failed.append(result)
            elif (result.raw or {}).get("reconciled"):
                submitted.append(result)
                warnings.append(
                    f"{order_req.symbol}: orden existente reconciliada "
                    f"(client_order_id={result.client_order_id}); sin fill nuevo."
                )
            else:
                submitted.append(result)
                # Audit + lifecycle mandate for buys
                try:
                    from database.engine import get_session
                    from services.audit_service import AuditService
                    from services.position_lifecycle_service import PositionLifecycleService

                    async for session in get_session():
                        await AuditService(session).record(
                            "buy_submit" if order_req.side == "buy" else "sell_submit",
                            actor="broker_execute",
                            symbol=order_req.symbol,
                            paper=self._broker.paper,
                            success=True,
                            message=f"{order_req.side} {order_req.qty} {order_req.symbol}",
                            payload={
                                "qty": order_req.qty,
                                "stop": order_req.stop_loss,
                                "tp": order_req.take_profit,
                                "order_id": result.id,
                            },
                        )
                        if order_req.side == "buy":
                            from services.live_safety import entry_price_from_fill

                            px = entry_price_from_fill(
                                result.filled_avg_price,
                                filled_qty=result.filled_qty,
                                limit_price=order_req.limit_price,
                                stop_loss=order_req.stop_loss,
                            )
                            thesis_txt = None
                            try:
                                from database.repositories.investment_memory_repository import (
                                    InvestmentMemoryRepository,
                                )

                                mem = await InvestmentMemoryRepository(session).latest_by_ticker(
                                    [order_req.symbol]
                                )
                                rec = mem.get(order_req.symbol)
                                if rec:
                                    thesis_txt = (
                                        f"{rec.recommendation}: {(rec.thesis or '')[:240]}"
                                    )
                            except Exception:
                                pass
                            if px is not None and px > 0:
                                await PositionLifecycleService(session, self).register_from_fill(
                                    symbol=order_req.symbol,
                                    qty=float(order_req.qty),
                                    entry_price=px,
                                    stop_loss=order_req.stop_loss,
                                    take_profit=order_req.take_profit,
                                    thesis=thesis_txt,
                                )
                        break
                except Exception as exc:
                    warnings.append(f"audit/lifecycle: {exc}")

        # Optional sync of NexBuy book after fills
        if request.sync_portfolio_id and submitted and not request.dry_run:
            try:
                from database.engine import get_session
                from services.reconcile_service import ReconcileService

                async for session in get_session():
                    await ReconcileService(session, self).reconcile(
                        sync=True, portfolio_id=request.sync_portfolio_id
                    )
                    break
            except Exception as exc:
                warnings.append(f"sync_portfolio falló: {exc}")

        logger.info(
            "alpaca.execute.done",
            paper=self._broker.paper,
            dry_run=request.dry_run,
            submitted=len(submitted),
            failed=len(failed),
            macro_mode=getattr(macro, "mode", None),
        )
        return ExecuteOrdersResponse(
            paper=self._broker.paper,
            dry_run=request.dry_run,
            submitted=submitted,
            failed=failed,
            warnings=warnings,
            request_ids=request_ids,
        )

    def lines_from_micro_plan(self, lines: list[dict[str, Any]] | list[Any]) -> list[ExecuteLine]:
        out: list[ExecuteLine] = []
        for line in lines:
            if hasattr(line, "model_dump"):
                data = line.model_dump()
            elif isinstance(line, dict):
                data = line
            else:
                continue
            shares = int(data.get("shares") or 0)
            if shares <= 0:
                continue
            out.append(
                ExecuteLine(
                    ticker=str(data["ticker"]).upper(),
                    shares=float(shares),
                    side="buy",
                    order_type="market",
                    stop_loss=data.get("stop_loss"),
                    take_profit=data.get("take_profit"),
                )
            )
        return out

    def _build_order_payload(self, req: BrokerOrderRequest) -> dict[str, Any]:
        qty = req.qty
        qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
        # Idempotency — same idea as alpaca CLI --client-order-id
        client_id = (req.client_order_id or "").strip()
        if not client_id:
            tag = "".join(c for c in (getattr(req, "source_tag", None) or "desk").lower() if c.isalnum())[:12] or "desk"
            if tag == "autopilot":
                from services.live_cycle_lock import live_client_order_id

                client_id = live_client_order_id(req.symbol, req.side)
            else:
                client_id = f"{tag}-{uuid4().hex[:12]}"
        payload: dict[str, Any] = {
            "symbol": req.symbol.upper(),
            "qty": qty_str,
            "side": req.side,
            "type": req.order_type,
            "time_in_force": req.time_in_force,
            "client_order_id": client_id[:48],
        }
        if req.extended_hours:
            payload["extended_hours"] = True
        if req.order_type in ("limit", "stop_limit") and req.limit_price is not None:
            payload["limit_price"] = str(req.limit_price)
        if req.order_type in ("stop", "stop_limit") and req.stop_price is not None:
            payload["stop_price"] = str(req.stop_price)

        if (
            req.side == "buy"
            and req.take_profit
            and req.stop_loss
            and req.order_type == "market"
        ):
            payload["order_class"] = "bracket"
            payload["take_profit"] = {"limit_price": str(round(req.take_profit, 2))}
            payload["stop_loss"] = {"stop_price": str(round(req.stop_loss, 2))}

        return payload

    def _map_account(self, raw: dict[str, Any]) -> BrokerAccount:
        return BrokerAccount(
            id=str(raw.get("id") or ""),
            status=str(raw.get("status") or ""),
            currency=str(raw.get("currency") or "USD"),
            cash=_f(raw.get("cash")),
            buying_power=_f(raw.get("buying_power")),
            portfolio_value=_f(raw.get("portfolio_value")),
            equity=_f(raw.get("equity")),
            pattern_day_trader=bool(raw.get("pattern_day_trader")),
            trading_blocked=bool(raw.get("trading_blocked")),
            account_blocked=bool(raw.get("account_blocked")),
            paper=self._broker.paper,
            raw={k: v for k, v in raw.items() if not str(k).startswith("_")},
        )

    def _map_position(self, raw: dict[str, Any]) -> BrokerPosition:
        return BrokerPosition(
            symbol=str(raw.get("symbol") or ""),
            qty=_f(raw.get("qty")),
            side=str(raw.get("side") or "long"),
            market_value=_f(raw.get("market_value")),
            avg_entry_price=_f(raw.get("avg_entry_price")),
            current_price=_f(raw.get("current_price")),
            unrealized_pl=_f(raw.get("unrealized_pl")),
            unrealized_plpc=_f(raw.get("unrealized_plpc")),
            asset_class=str(raw.get("asset_class") or "us_equity"),
        )

    def _map_order(self, raw: dict[str, Any]) -> BrokerOrderResult:
        return BrokerOrderResult(
            id=str(raw.get("id") or ""),
            client_order_id=str(raw.get("client_order_id") or ""),
            symbol=str(raw.get("symbol") or ""),
            qty=_f(raw.get("qty")),
            filled_qty=_f(raw.get("filled_qty")),
            side=str(raw.get("side") or ""),
            type=str(raw.get("type") or raw.get("order_type") or ""),
            status=str(raw.get("status") or ""),
            submitted_at=_parse_dt(raw.get("submitted_at")),
            filled_avg_price=_f(raw.get("filled_avg_price")) if raw.get("filled_avg_price") else None,
            request_id=raw.get("_request_id") or self._broker.last_request_id,
            raw={k: v for k, v in raw.items() if not str(k).startswith("_")},
        )
