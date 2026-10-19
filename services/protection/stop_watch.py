"""Client-side stop / TP ladder watcher (PAPER only by default).

Rules (long positions):
  * px <= stop                -> close position (cancel backup stop first).
  * px >= TP1 (stage 0)       -> stop := entry (breakeven), stage 1.
  * px >= TP2 (stage 1)       -> stop := TP1, stage 2.
  * px >= TP3 (stage 2)       -> close remainder, stage 3.
  * Stop only ratchets up, never down.

Every open position must always have a broker-side backup stop equal to the
current ladder stop: equities -> ``stop`` GTC (PATCH on change); crypto ->
``stop_limit`` GTC (cancel + recreate on change; Alpaca crypto has no
stop/OTO/bracket/trailing). If a backup stop cannot be placed the position is
closed and an alert is raised. A heartbeat file lets an external watchdog
detect a dead watcher.

Hard guard: every order-sending path re-checks that the broker base URL host is
``paper-api.alpaca.markets`` and the account is not ``paper=false`` unless the
explicit LIVE acknowledgement is set (never in this change).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from utils.logging import get_logger

logger = get_logger(__name__)

PAPER_HOST = "paper-api.alpaca.markets"
DEFAULT_STATE = Path(os.environ.get("CLIENT_STOP_WATCH_STATE", "data/stop_watch_state.json"))
DEFAULT_HEARTBEAT = Path(os.environ.get("CLIENT_STOP_WATCH_HEARTBEAT", "data/stop_watch_heartbeat.json"))

QuoteFn = Callable[[str], Awaitable[float]]
AlertFn = Callable[[str, dict], Awaitable[None] | None]


class NotPaperError(RuntimeError):
    pass


class PositionGoneError(RuntimeError):
    """The broker no longer holds the tracked position (qty 0)."""


def is_insufficient_balance(exc: BaseException) -> bool:
    """Alpaca 403 'insufficient balance ... available: 0' on a sell."""
    msg = str(exc).lower()
    return "insufficient" in msg and ("balance" in msg or "qty" in msg or "available" in msg)


def is_crypto_symbol(symbol: str, asset_class: str | None = None) -> bool:
    if asset_class:
        return asset_class.lower() == "crypto"
    s = symbol.upper()
    return "/" in s or (s.endswith("USD") and len(s) > 5)


def _bool_env(name: str) -> bool | None:
    v = os.environ.get(name)
    if v is None or v == "":
        return None
    return v.strip().lower() in {"1", "true", "yes", "on"}


def stop_watch_enabled(paper: bool) -> bool:
    """Flag CLIENT_STOP_WATCH_ENABLED. Default on in paper, OFF in LIVE.

    In LIVE it stays off even if the flag is true, unless CLIENT_STOP_WATCH_LIVE_ACK=true.
    """
    flag = _bool_env("CLIENT_STOP_WATCH_ENABLED")
    if not paper:
        return bool(flag) and bool(_bool_env("CLIENT_STOP_WATCH_LIVE_ACK"))
    return True if flag is None else flag


def assert_paper_broker_url(base_url: Any) -> None:
    raw = str(base_url or "")
    host = (urlparse(raw if "://" in raw else f"https://{raw}").hostname or "").lower()
    if host != PAPER_HOST:
        raise NotPaperError(f"stop_watch: base URL no es paper ({host or 'vacía'}); abortado")


async def assert_paper(broker: Any) -> None:
    assert_paper_broker_url(getattr(broker, "base_url", None))
    acct = await broker.get_account()
    if not isinstance(acct, dict):
        raise NotPaperError("stop_watch: cuenta ilegible; abortado")
    if acct.get("paper") is False:
        raise NotPaperError("stop_watch: cuenta paper=false; abortado")
    # Alpaca paper account numbers start with "PA"
    num = str(acct.get("account_number") or "")
    if num and not num.upper().startswith("PA"):
        raise NotPaperError("stop_watch: número de cuenta no es paper; abortado")


def round_px(px: float) -> float:
    if px >= 1:
        return round(px, 2)
    return round(px, 6)


@dataclass
class ProtectedPosition:
    symbol: str
    asset_class: str  # equity | crypto
    qty: float
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    stage: int = 0
    broker_stop_order_id: str | None = None
    broker_stop_price: float | None = None
    broker_stop_qty: float | None = None
    peak: float = 0.0
    closed: bool = False
    updated_at: float = field(default_factory=time.time)
    history: list = field(default_factory=list)

    @classmethod
    def from_entry(
        cls,
        symbol: str,
        asset_class: str,
        qty: float,
        entry: float,
        stop: float,
        ladder_r: tuple[float, float, float] = (1.0, 2.0, 3.0),
        tps: tuple[float, float, float] | None = None,
    ) -> "ProtectedPosition":
        r = entry - stop
        if r <= 0:
            raise ValueError("stop debe estar por debajo de la entrada (long)")
        t1, t2, t3 = tps or tuple(round_px(entry + k * r) for k in ladder_r)
        return cls(symbol, asset_class, qty, entry, round_px(stop), t1, t2, t3, peak=entry)


def next_ladder(pos: ProtectedPosition, px: float) -> tuple[str, float]:
    """Pure decision. Returns (action, new_stop); action in hold|raise|close_stop|close_tp3."""
    if px <= pos.stop:
        return "close_stop", pos.stop
    new_stop, stage = pos.stop, pos.stage
    if stage < 1 and px >= pos.tp1:
        new_stop, stage = max(new_stop, pos.entry), 1
    if stage < 2 and px >= pos.tp2:
        new_stop, stage = max(new_stop, pos.tp1), 2
    if stage < 3 and px >= pos.tp3:
        return "close_tp3", new_stop
    pos.stage = stage
    if new_stop > pos.stop:
        return "raise", new_stop
    return "hold", pos.stop


class StopWatchService:
    def __init__(
        self,
        broker: Any,
        quote: QuoteFn,
        *,
        alert: AlertFn | None = None,
        state_path: Path = DEFAULT_STATE,
        heartbeat_path: Path = DEFAULT_HEARTBEAT,
        default_stop_pct: float = 0.03,
        crypto_slippage_pct: float | None = None,
        paper: bool = True,
        only_symbols: set[str] | None = None,
        file_backend: bool = True,
        enabled_override: bool | None = None,
    ) -> None:
        # file_backend=False: state/heartbeat live in memory and the caller persists them
        # (in-app job -> DB via services/protection/stop_watch_job.py). Paper script keeps files.
        self.file_backend = file_backend
        self._enabled_override = enabled_override
        self.last_beat: dict | None = None
        self.only = {x.replace('/', '').upper() for x in only_symbols} if only_symbols else None
        self._b = broker
        self._quote = quote
        self._alert_fn = alert
        self.state_path = Path(state_path)
        self.heartbeat_path = Path(heartbeat_path)
        self.default_stop_pct = default_stop_pct
        self.slip = (
            crypto_slippage_pct
            if crypto_slippage_pct is not None
            else float(os.environ.get("CRYPTO_BACKUP_STOP_LIMIT_SLIPPAGE_PCT", "0.01"))
        )
        self.paper = paper
        self.positions: dict[str, ProtectedPosition] = {}
        self._seq = 0
        self.load()

    # ---------- persistence ----------
    def load(self) -> None:
        if not self.file_backend:
            return
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text() or "{}")
            self.positions = {k: ProtectedPosition(**v) for k, v in data.items()}

    def dump_state(self) -> dict:
        return {k: asdict(v) for k, v in self.positions.items()}

    def load_state(self, data: dict | None) -> None:
        self.positions = {k: ProtectedPosition(**v) for k, v in (data or {}).items()}

    def save(self) -> None:
        if not self.file_backend:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(v) for k, v in self.positions.items()}, indent=2))
        tmp.replace(self.state_path)

    def beat(self, extra: dict | None = None) -> None:
        self.last_beat = {"ts": time.time(), **(extra or {})}
        if not self.file_backend:
            return
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(json.dumps({"ts": time.time(), **(extra or {})}))

    async def alert(self, kind: str, data: dict) -> None:
        logger.warning("stop_watch.alert", kind=kind, **data)
        if self._alert_fn:
            res = self._alert_fn(kind, data)
            if hasattr(res, "__await__"):
                await res

    # ---------- guards ----------
    def heartbeat_age(self) -> float | None:
        if not self.file_backend:
            try:
                return time.time() - float((self.last_beat or {})["ts"])
            except Exception:
                return None
        return heartbeat_age(self.heartbeat_path)

    def enabled(self) -> bool:
        if self._enabled_override is not None:
            # LIVE still needs the explicit ack even with an override
            if not self.paper:
                return bool(self._enabled_override) and bool(_bool_env("CLIENT_STOP_WATCH_LIVE_ACK"))
            return bool(self._enabled_override)
        return stop_watch_enabled(self.paper)

    async def _guard(self) -> None:
        if not self.enabled():
            raise NotPaperError("stop_watch deshabilitado (LIVE o flag off)")
        if self.paper:
            await assert_paper(self._b)

    def _cid(self, sym: str, tag: str) -> str:
        self._seq += 1
        return f"sw-{sym.replace('/', '')}-{tag}-{int(time.time())}-{self._seq}"

    # ---------- broker reconciliation ----------
    async def _broker_qty(self, pos: ProtectedPosition) -> float | None:
        """Total qty held at the broker for pos; None if it could not be read."""
        try:
            rows = await self._b.get_positions()
        except Exception as exc:
            logger.info("stop_watch.positions_read_failed", symbol=pos.symbol, error=str(exc))
            return None
        key = pos.symbol.replace("/", "").upper()
        for p in rows or []:
            if str(p.get("symbol", "")).replace("/", "").upper() == key:
                return float(p.get("qty") or 0)
        return 0.0

    async def _stop_fill(self, pos: ProtectedPosition) -> dict | None:
        """Recent fill of the known broker stop order, if any."""
        if not pos.broker_stop_order_id:
            return None
        try:
            rows = await self._b.list_orders(status="closed", limit=200)
        except Exception:
            return None
        for o in rows or []:
            if str(o.get("id")) == str(pos.broker_stop_order_id) and str(o.get("status")) in {"filled", "partially_filled"}:
                return o
        return None

    async def mark_gone(self, pos: ProtectedPosition, fill: dict | None = None, reason: str = "gone_at_broker") -> None:
        if fill is None:
            fill = await self._stop_fill(pos)
        ev: dict[str, Any] = {"ts": time.time(), "event": "gone_at_broker", "reason": reason}
        if fill:
            try:
                ev["fill_price"] = float(fill.get("filled_avg_price") or 0) or None
            except (TypeError, ValueError):
                ev["fill_price"] = None
            ev["fill_order_id"] = str(fill.get("id"))
        pos.closed = True
        pos.broker_stop_order_id = None
        pos.history.append(ev)
        logger.info("stop_watch.gone", symbol=pos.symbol, fill_price=ev.get("fill_price"), reason=reason)

    async def reconcile_gone(self, pos: ProtectedPosition) -> bool:
        """True (and pos marked closed) if the broker no longer holds the position."""
        if pos.closed:
            return True
        qty = await self._broker_qty(pos)
        if qty is not None and qty <= 1e-12:
            await self.mark_gone(pos)
            return True
        return False

    # ---------- broker helpers ----------
    async def _place_backup(self, pos: ProtectedPosition) -> dict:
        await self._guard()
        stop = round_px(pos.stop)
        order: dict[str, Any] = {
            "symbol": pos.symbol,
            "side": "sell",
            "qty": str(pos.qty),
            "time_in_force": "gtc",
            "client_order_id": self._cid(pos.symbol, f"s{pos.stage}"),
        }
        if pos.asset_class == "crypto":
            order.update(type="stop_limit", stop_price=str(stop), limit_price=str(round_px(stop * (1 - self.slip))))
        else:
            order.update(type="stop", stop_price=str(stop))
        raw = await self._b.submit_order(order)
        if not isinstance(raw, dict) or str(raw.get("status") or "") in {"rejected", "canceled", "expired"}:
            raise RuntimeError(f"backup stop rechazado: {raw.get('status') if isinstance(raw, dict) else raw}")
        pos.broker_stop_order_id = str(raw.get("id") or "") or None
        pos.broker_stop_price = stop
        pos.broker_stop_qty = pos.qty
        return raw

    async def ensure_backup(self, pos: ProtectedPosition, retries: int = 2) -> bool:
        """Make the broker stop match pos.stop/qty. On failure: close + alert."""
        if pos.closed:
            return False
        await self._guard()
        # re-placing (not a fresh registration right after a fill, where the
        # positions endpoint may still lag): verify the broker still holds it
        replacing = bool(pos.broker_stop_order_id or pos.history)
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                if pos.broker_stop_order_id and pos.broker_stop_price == round_px(pos.stop) and pos.broker_stop_qty == pos.qty:
                    return True
                if replacing and await self.reconcile_gone(pos):
                    return False
                if pos.broker_stop_order_id and pos.asset_class != "crypto":
                    await self._guard()
                    raw = await self._b.replace_order(
                        pos.broker_stop_order_id, stop_price=round_px(pos.stop), qty=pos.qty
                    )
                    pos.broker_stop_order_id = str(raw.get("id") or pos.broker_stop_order_id)
                    pos.broker_stop_price, pos.broker_stop_qty = round_px(pos.stop), pos.qty
                    return True
                if pos.broker_stop_order_id:
                    await self._guard()
                    try:
                        await self._b.cancel_order(pos.broker_stop_order_id)
                    except Exception as exc:  # already filled/canceled
                        logger.info("stop_watch.cancel_old_failed", symbol=pos.symbol, error=str(exc))
                    pos.broker_stop_order_id = None
                await self._place_backup(pos)
                return True
            except NotPaperError:
                raise
            except Exception as exc:
                last = exc
                pos.broker_stop_order_id = None if pos.asset_class == "crypto" else pos.broker_stop_order_id
                if is_insufficient_balance(exc):
                    # nothing to sell: re-check the position instead of retrying
                    if await self.reconcile_gone(pos):
                        return False
                    await self.alert("insufficient_balance_on_stop", {"symbol": pos.symbol, "error": str(exc)})
                    return False
        if await self.reconcile_gone(pos):
            return False
        await self.alert("backup_stop_failed_closing", {"symbol": pos.symbol, "error": str(last)})
        await self.close(pos, reason="backup_stop_failed")
        return False

    async def close(self, pos: ProtectedPosition, reason: str) -> None:
        await self._guard()
        if pos.closed:
            return
        fill = await self._stop_fill(pos)
        if fill is not None or await self.reconcile_gone(pos):
            if not pos.closed:
                await self.mark_gone(pos, fill=fill, reason=f"{reason}:stop_filled")
            return
        if pos.broker_stop_order_id:
            try:
                await self._b.cancel_order(pos.broker_stop_order_id)
            except Exception as exc:
                logger.info("stop_watch.cancel_before_close_failed", symbol=pos.symbol, error=str(exc))
            pos.broker_stop_order_id = None
        try:
            await self._b.submit_order(
                {
                    "symbol": pos.symbol,
                    "side": "sell",
                    "type": "market",
                    "qty": str(pos.qty),
                    "time_in_force": "gtc" if pos.asset_class == "crypto" else "day",
                    "client_order_id": self._cid(pos.symbol, "close"),
                }
            )
        except Exception as exc:
            if is_insufficient_balance(exc):
                if await self.reconcile_gone(pos):
                    return
                await self.alert("insufficient_balance_on_close", {"symbol": pos.symbol, "error": str(exc)})
                return
            raise
        pos.closed = True
        pos.history.append({"ts": time.time(), "event": "close", "reason": reason})
        logger.info("stop_watch.closed", symbol=pos.symbol, reason=reason)

    # ---------- registration ----------
    async def register(self, pos: ProtectedPosition) -> ProtectedPosition:
        self.positions[pos.symbol] = pos
        await self.ensure_backup(pos)
        pos.history.append({"ts": time.time(), "event": "register", "stop": pos.stop})
        self.save()
        return pos

    async def adopt_existing_stop(self, pos: ProtectedPosition, orders: list[dict]) -> None:
        for o in orders:
            if (
                str(o.get("symbol", "")).replace("/", "") == pos.symbol.replace("/", "")
                and o.get("side") == "sell"
                and o.get("type") in {"stop", "stop_limit"}
                and o.get("status") in {"new", "accepted", "held", "pending_new", "partially_filled"}
            ):
                pos.broker_stop_order_id = str(o.get("id"))
                pos.broker_stop_price = round_px(float(o.get("stop_price") or 0))
                pos.broker_stop_qty = float(o.get("qty") or 0)
                if pos.broker_stop_price > pos.stop:
                    pos.stop = pos.broker_stop_price  # never lower
                return

    # ---------- main loop ----------
    async def tick(self) -> dict:
        await self._guard()
        broker_pos = {
            str(p["symbol"]).replace("/", ""): p
            for p in await self._b.get_positions()
            if self.only is None or str(p["symbol"]).replace("/", "").upper() in self.only
        }
        open_orders = await self._b.list_orders(status="open", limit=200)
        report: dict[str, Any] = {"checked": 0, "actions": []}
        # orphans: broker position without a record -> default stop + alert
        for key, bp in broker_pos.items():
            sym = next((s for s in self.positions if s.replace("/", "") == key and not self.positions[s].closed), None)
            if sym is None:
                entry = float(bp.get("avg_entry_price") or 0)
                ac = "crypto" if str(bp.get("asset_class")) == "crypto" else "equity"
                qty = float(bp.get("qty_available") or bp.get("qty") or 0)
                if entry <= 0 or qty <= 0:
                    continue
                pos = ProtectedPosition.from_entry(
                    _canon(str(bp["symbol"]), ac), ac, qty, entry, entry * (1 - self.default_stop_pct)
                )
                await self.adopt_existing_stop(pos, open_orders)
                await self.alert("orphan_position", {"symbol": pos.symbol, "stop": pos.stop})
                self.positions[pos.symbol] = pos
                await self.ensure_backup(pos)
                report["actions"].append({"symbol": pos.symbol, "action": "adopted"})
        for sym, pos in list(self.positions.items()):
            if pos.closed:
                continue
            bp = broker_pos.get(sym.replace("/", ""))
            if bp is None or float(bp.get("qty") or 0) <= 1e-12:  # broker stop filled or closed elsewhere
                await self.mark_gone(pos)
                report["actions"].append({"symbol": sym, "action": "gone"})
                continue
            qty_b = float(bp.get("qty_available") or bp.get("qty") or 0)
            # qty_available excludes qty reserved by our own stop; use total qty
            qty_total = float(bp.get("qty") or qty_b)
            if qty_total and abs(qty_total - pos.qty) > 1e-9:
                pos.qty = qty_total
            report["checked"] += 1
            live_ids = {str(o.get("id")) for o in open_orders}
            if pos.broker_stop_order_id and pos.broker_stop_order_id not in live_ids:
                # backup stop vanished at broker (manual cancel/expired): re-place now
                await self.alert("backup_stop_missing", {"symbol": sym, "order_id": pos.broker_stop_order_id})
                pos.broker_stop_order_id = None
                pos.broker_stop_price = None
            px = float(await self._quote(sym) or 0)
            if px <= 0:
                await self.alert("no_quote", {"symbol": sym})
                await self.ensure_backup(pos)
                continue
            pos.peak = max(pos.peak, px)
            old = pos.stop
            action, new_stop = next_ladder(pos, px)
            if action in {"close_stop", "close_tp3"}:
                await self.close(pos, reason=action)
            elif action == "raise":
                pos.stop = max(pos.stop, round_px(new_stop))
                pos.history.append({"ts": time.time(), "event": "raise", "old": old, "new": pos.stop, "stage": pos.stage})
                await self.ensure_backup(pos)
            else:
                await self.ensure_backup(pos)
            pos.updated_at = time.time()
            report["actions"].append({"symbol": sym, "action": action, "px": px, "stop": pos.stop, "stage": pos.stage})
        self.save()
        self.beat({"checked": report["checked"]})
        return report


def _canon(sym: str, ac: str) -> str:
    if ac == "crypto" and "/" not in sym and sym.upper().endswith("USD"):
        return f"{sym[:-3]}/USD"
    return sym


def heartbeat_age(path: Path = DEFAULT_HEARTBEAT) -> float | None:
    try:
        return time.time() - float(json.loads(Path(path).read_text())["ts"])
    except Exception:
        return None


async def watchdog_check(
    svc: StopWatchService, max_age_s: float | None = None
) -> dict:
    """If the heartbeat is stale: alert and verify every position has a broker stop."""
    max_age = max_age_s if max_age_s is not None else float(os.environ.get("CLIENT_STOP_WATCH_HEARTBEAT_MAX_S", "90"))
    age = svc.heartbeat_age()
    if age is not None and age <= max_age:
        return {"stale": False, "age": age}
    await svc.alert("stop_watch_heartbeat_stale", {"age": age})
    fixed = []
    for pos in svc.positions.values():
        if not pos.closed:
            # startup reconciliation: never re-place a stop for a position already gone
            if await svc.reconcile_gone(pos):
                continue
            pos.broker_stop_order_id = await _find_live_stop(svc, pos)
            if await svc.ensure_backup(pos):
                fixed.append(pos.symbol)
    svc.save()
    return {"stale": True, "age": age, "verified": fixed}


async def _find_live_stop(svc: StopWatchService, pos: ProtectedPosition) -> str | None:
    try:
        orders = await svc._b.list_orders(status="open", limit=200)
    except Exception:
        return pos.broker_stop_order_id
    for o in orders:
        if str(o.get("id")) == str(pos.broker_stop_order_id):
            return pos.broker_stop_order_id
    pos.broker_stop_price = None
    return None
