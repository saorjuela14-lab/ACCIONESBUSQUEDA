"""Daily open/close portfolio status briefing for WhatsApp (and other push channels)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from config.settings import get_settings
from services.alpaca_order_service import AlpacaOrderService
from services.push_notification_service import PushNotificationService
from services.risk_policy_service import RiskPolicyService
from utils.logging import get_logger

logger = get_logger(__name__)

ET = ZoneInfo("America/New_York")
SessionKind = Literal["open", "close", "manual"]


class DailyStatusBriefingService:
    """Builds and sends a firm-style status digest at market open/close."""

    def __init__(
        self,
        broker: AlpacaOrderService | None = None,
        push: PushNotificationService | None = None,
    ) -> None:
        self._broker = broker or AlpacaOrderService()
        self._push = push or PushNotificationService()
        self._settings = get_settings()

    async def build(self, session_kind: SessionKind = "manual") -> tuple[str, str]:
        """Return (title, plain-text body)."""
        now = datetime.now(ET)
        label = {
            "open": "APERTURA",
            "close": "CIERRE",
            "manual": "STATUS",
        }.get(session_kind, "STATUS")
        title = f"NexBuy {label} · {now.strftime('%d %b %Y %H:%M ET')}"

        lines: list[str] = [title, ""]

        if not self._broker.is_configured():
            lines.append("Alpaca no configurada — sin datos de cuenta.")
            return title, "\n".join(lines)

        try:
            account = await self._broker.get_account()
            mode = "PAPER" if account.paper else "LIVE"
            lines.append(f"Cuenta {mode} · {account.status or 'OK'}")
            lines.append(
                f"Equity ${account.equity:,.2f} · Cash ${account.cash:,.2f} · "
                f"Buying power ${account.buying_power:,.2f}"
            )
        except Exception as exc:
            lines.append(f"Cuenta: error ({exc})")
            account = None

        try:
            risk = await RiskPolicyService().status()
            lines.append(
                f"Risk desk: {risk.macro.mode} · trading "
                f"{'OK' if risk.macro.trading_allowed else 'BLOQUEADO'}"
            )
            if risk.macro.block_reason:
                lines.append(f"Motivo: {risk.macro.block_reason}")
        except Exception:
            pass

        lines.append(
            f"Firma autónoma: {'ON' if self._settings.firm_autonomy else 'OFF'} · "
            f"auto-exec {'ON' if (self._settings.auto_execute_trades or self._settings.firm_autonomy) else 'OFF'}"
        )
        lines.append("")

        # Positions
        try:
            positions = await self._broker.get_positions()
        except Exception as exc:
            positions = []
            lines.append(f"Posiciones: error ({exc})")
        else:
            lines.append(f"POSICIONES ({len(positions)})")
            if not positions:
                lines.append("· ninguna abierta")
            else:
                for p in sorted(positions, key=lambda x: abs(x.market_value), reverse=True)[:12]:
                    pl = p.unrealized_pl
                    plpc = p.unrealized_plpc
                    # Alpaca often returns plpc as fraction
                    if abs(plpc) <= 1.5:
                        plpc *= 100
                    sign = "+" if pl >= 0 else ""
                    lines.append(
                        f"· {p.symbol}: {p.qty:g} @ ${p.current_price:.4g} "
                        f"= ${p.market_value:,.2f} ({sign}{pl:,.2f} / {sign}{plpc:.1f}%)"
                    )
            lines.append("")

        # Open orders
        try:
            open_orders = await self._broker.list_orders(status="open", limit=30)
        except Exception as exc:
            open_orders = []
            lines.append(f"Órdenes abiertas: error ({exc})")
        else:
            lines.append(f"ÓRDENES ABIERTAS ({len(open_orders)})")
            if not open_orders:
                lines.append("· ninguna pendiente")
            else:
                for o in open_orders[:15]:
                    lines.append(
                        f"· {o.side.upper()} {o.qty:g} {o.symbol} "
                        f"({o.type}/{o.status})"
                    )
            lines.append("")

        # Closed / filled today (closed book)
        try:
            closed = await self._broker.list_orders(status="closed", limit=40)
        except Exception as exc:
            closed = []
            lines.append(f"Órdenes cerradas: error ({exc})")
        else:
            today = now.date()
            todays = []
            for o in closed:
                ts = o.submitted_at or o.raw.get("filled_at") or o.raw.get("updated_at")
                if ts is None:
                    continue
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts.astimezone(ET).date() == today:
                    todays.append(o)
            lines.append(f"ÓRDENES CERRADAS HOY ({len(todays)})")
            if not todays:
                lines.append("· sin fills/cancelaciones hoy")
            else:
                for o in todays[:15]:
                    px = f" @ ${o.filled_avg_price:.4g}" if o.filled_avg_price else ""
                    filled = o.filled_qty or o.qty
                    lines.append(
                        f"· {o.side.upper()} {filled:g} {o.symbol}{px} — {o.status}"
                    )
            lines.append("")

        if session_kind == "open":
            lines.append("Gestión: Autopilot revisará compras solo con consenso del comité.")
        elif session_kind == "close":
            lines.append("Gestión: lifecycle/risk revisaron salidas; resumen de fin de sesión.")
        else:
            lines.append("Gestión: status manual bajo demanda.")

        lines.append("— NexBuy desk")
        return title, "\n".join(lines)

    async def send(
        self,
        session_kind: SessionKind = "manual",
        *,
        whatsapp_only: bool = False,
    ) -> dict:
        """Build briefing and push to configured channels."""
        title, body = await self.build(session_kind)
        if whatsapp_only:
            sent = await self._push.notify_whatsapp_plain(f"{title}\n\n{body}")
            result = {"whatsapp": sent, "title": title}
        else:
            result = await self._push.notify_message(
                title,
                body,
                prefer_plain=True,
                channels=("telegram", "whatsapp", "webhook"),
            )
            result["title"] = title
        logger.info(
            "daily_status_briefing.sent",
            session=session_kind,
            **{k: v for k, v in result.items() if k != "title"},
        )
        return result
