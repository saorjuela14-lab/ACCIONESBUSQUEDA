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

    def __init__(self, broker: AlpacaBrokerProvider | None = None) -> None:
        self._broker = broker or get_broker_provider()

    @property
    def paper(self) -> bool:
        return self._broker.paper

    def is_configured(self) -> bool:
        return self._broker.is_configured()

    async def status(self) -> BrokerStatus:
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

    async def list_orders(self, status: str = "open", limit: int = 50) -> list[BrokerOrderResult]:
        raw_list = await self._broker.list_orders(status=status, limit=limit)
        return [self._map_order(o) for o in raw_list]

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._broker.cancel_order(order_id)

    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        return await self._broker.cancel_all_orders()

    async def close_position(self, symbol: str) -> dict[str, Any]:
        return await self._broker.close_position(symbol)

    async def close_all_positions(self, *, cancel_orders: bool = True) -> list[dict[str, Any]]:
        return await self._broker.close_all_positions(cancel_orders=cancel_orders)

    async def submit_one(self, req: BrokerOrderRequest) -> BrokerOrderResult:
        payload = self._build_order_payload(req)
        try:
            raw = await self._broker.submit_order(payload)
            return self._map_order(raw)
        except Exception as exc:
            return BrokerOrderResult(
                symbol=req.symbol.upper(),
                qty=req.qty,
                side=req.side,
                type=req.order_type,
                status="failed",
                client_order_id=str(payload.get("client_order_id") or ""),
                request_id=self._broker.last_request_id,
                error=str(exc),
            )

    async def execute(self, request: ExecuteOrdersRequest) -> ExecuteOrdersResponse:
        warnings: list[str] = []
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

        account = None
        try:
            account = await self.get_account()
            if account.cash <= 0 and account.buying_power <= 0 and not request.dry_run:
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

        for line in request.lines:
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

            order_req = BrokerOrderRequest(
                symbol=line.ticker.upper().strip(),
                qty=line.shares,
                side=line.side,
                order_type=line.order_type,
                limit_price=line.limit_price,
                take_profit=line.take_profit,
                stop_loss=line.stop_loss,
                client_order_id=line.client_order_id,
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

            result = await self.submit_one(order_req)
            if result.request_id:
                request_ids.append(result.request_id)
            if result.error or result.status == "failed":
                failed.append(result)
            else:
                submitted.append(result)

        logger.info(
            "alpaca.execute.done",
            paper=self._broker.paper,
            dry_run=request.dry_run,
            submitted=len(submitted),
            failed=len(failed),
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
        client_id = (req.client_order_id or "").strip() or f"nexbuy-{uuid4()}"
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
