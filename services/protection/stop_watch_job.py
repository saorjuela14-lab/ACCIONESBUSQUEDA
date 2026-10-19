"""In-app runner for the client stop watcher (one tick per scheduler run).

- Runs only when ``settings.client_stop_watch_enabled`` is True (default False).
- Guarded by a DB lease (``client_stop_watch``) so only one replica ticks.
- State + heartbeat persisted in ``ops_flags`` (no migration); the paper box script
  keeps its file backend.
- First tick of a process (or after a stall) runs ``watchdog_check`` = startup
  reconciliation against the broker.
"""

from __future__ import annotations

import time
from typing import Any

from config.settings import get_settings
from utils.logging import get_logger

logger = get_logger(__name__)

LEASE_CLIENT_STOP_WATCH = "client_stop_watch"
STATE_KEY = "client_stop_watch_state"
HEARTBEAT_KEY = "client_stop_watch_heartbeat"
LEASE_TTL_SECONDS = 120

_reconciled_in_process = False


def job_enabled(settings: Any = None) -> bool:
    s = settings or get_settings()
    return getattr(s, "client_stop_watch_enabled", False) is True


def _paper_quote_fn(broker: Any):
    """Latest BID; 0 if older than 120 s (watcher keeps the broker backup stop)."""
    from datetime import datetime, timezone

    import httpx

    data = "https://data.alpaca.markets"

    async def quote(sym: str) -> float:
        async with httpx.AsyncClient(timeout=15) as c:
            if "/" in sym:
                r = await c.get(
                    f"{data}/v1beta3/crypto/us/latest/quotes",
                    params={"symbols": sym},
                    headers=broker._headers(),
                )
                q = r.json()["quotes"][sym]
            else:
                r = await c.get(
                    f"{data}/v2/stocks/{sym}/quotes/latest",
                    params={"feed": "iex"},
                    headers=broker._headers(),
                )
                q = r.json()["quote"]
        raw = str(q["t"]).replace("Z", "+00:00")
        if "." in raw:
            raw = raw.split(".")[0] + "+00:00"
        t = datetime.fromisoformat(raw)
        if (datetime.now(timezone.utc) - t).total_seconds() > 120:
            return 0.0
        return float(q.get("bp") or 0)

    return quote


def _default_service_factory():
    from services.multiasset.paper_broker import get_beta_broker_provider
    from services.protection.stop_watch import StopWatchService

    broker = get_beta_broker_provider()

    async def alert(kind: str, data: dict) -> None:
        from services.desk_ops_alert import emit_desk_ops_alert

        await emit_desk_ops_alert(
            f"stop_watch_{kind}" if not kind.startswith("stop_watch") else kind,
            detail=str(data)[:400],
            dedupe_key=f"stop_watch:{kind}:{data.get('symbol', '')}",
        )

    return StopWatchService(
        broker,
        _paper_quote_fn(broker),
        alert=alert,
        file_backend=False,
        enabled_override=True,
        paper=True,
    )


async def run_stop_watch_tick(
    session_factory=None, service_factory=None, settings: Any = None
) -> dict:
    """One guarded tick. Returns a small report dict (never raises)."""
    global _reconciled_in_process
    s = settings or get_settings()
    if not job_enabled(s):
        return {"skipped": "disabled"}
    from database.repositories.ops_repository import OpsFlagRepository
    from services.db_lease import acquire_lease, release_lease, run_owner

    if session_factory is None:
        from database.engine import get_session as session_factory  # type: ignore
    owner = run_owner()
    async for session in session_factory():
        try:
            snap = await acquire_lease(
                session, name=LEASE_CLIENT_STOP_WATCH, owner=owner, ttl_seconds=LEASE_TTL_SECONDS
            )
        except Exception as exc:
            logger.warning("stop_watch_job.lease_failed", error=str(exc))
            return {"skipped": "lease_failed", "error": str(exc)}
        if not snap.acquired:
            return {"skipped": "lease_held", "owner": snap.owner}
        repo = OpsFlagRepository(session)
        try:
            svc = (service_factory or _default_service_factory)()
            svc.load_state(await repo.get_json(STATE_KEY))
            hb = await repo.get_json(HEARTBEAT_KEY)
            svc.last_beat = hb if hb.get("ts") else None
            from services.protection.stop_watch import watchdog_check

            wd = None
            if not _reconciled_in_process:
                # startup reconciliation: force the check on the first tick of this process
                svc.last_beat = None
            wd = await watchdog_check(svc, max_age_s=float(s.client_stop_watch_heartbeat_max_s))
            _reconciled_in_process = True
            rep = await svc.tick()
            await repo.set_json(STATE_KEY, svc.dump_state())
            await repo.set_json(
                HEARTBEAT_KEY,
                {**(svc.last_beat or {"ts": time.time()}), "owner": owner, "checked": rep.get("checked")},
            )
            return {"ok": True, "watchdog": wd, **rep}
        except Exception as exc:
            logger.warning("stop_watch_job.tick_failed", error=f"{type(exc).__name__}: {exc}")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                await release_lease(session, name=LEASE_CLIENT_STOP_WATCH, owner=owner)
            except Exception:
                pass
    return {"skipped": "no_session"}


async def stop_watch_status(session, settings: Any = None, now: float | None = None) -> dict:
    """Heartbeat status for /ops/status. stale only matters while enabled."""
    from database.repositories.ops_repository import OpsFlagRepository

    s = settings or get_settings()
    enabled = job_enabled(s)
    hb = await OpsFlagRepository(session).get_json(HEARTBEAT_KEY)
    ts = hb.get("ts")
    age = (float(now or time.time()) - float(ts)) if ts else None
    limit = float(getattr(s, "client_stop_watch_stale_alert_s", 120))
    stale = bool(enabled and (age is None or age > limit))
    return {
        "enabled": enabled,
        "last_tick_ts": ts,
        "age_s": round(age, 1) if age is not None else None,
        "stale_after_s": limit,
        "stale": stale,
        "owner": hb.get("owner"),
    }


async def stop_watch_staleness_check(session_factory=None, settings: Any = None) -> dict:
    """Watchdog job: alert via desk_ops_alert if heartbeat stale > limit while enabled."""
    s = settings or get_settings()
    if not job_enabled(s):
        return {"skipped": "disabled"}
    if session_factory is None:
        from database.engine import get_session as session_factory  # type: ignore
    async for session in session_factory():
        st = await stop_watch_status(session, s)
        if st["stale"]:
            from services.desk_ops_alert import emit_desk_ops_alert

            await emit_desk_ops_alert(
                "stop_watch_heartbeat_stale",
                detail=f"client stop_watch sin tick hace {st['age_s']} s (límite {st['stale_after_s']} s)",
                dedupe_key="stop_watch_heartbeat_stale",
            )
        return st
    return {"skipped": "no_session"}
