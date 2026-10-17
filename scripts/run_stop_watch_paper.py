"""Run the client stop watcher against Alpaca PAPER only. Usage: python scripts/run_stop_watch_paper.py"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from providers.broker.alpaca_provider import AlpacaBrokerProvider  # noqa: E402
from services.protection.stop_watch import (  # noqa: E402
    StopWatchService,
    assert_paper,
    assert_paper_broker_url,
    watchdog_check,
)

BASE = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
LOGDIR = Path(os.environ.get("STOP_WATCH_LOGDIR", "/workspace/monarch-lider/stop_watch_paper"))


def log(**kw):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(LOGDIR / "watch.log", "a") as f:
        f.write(json.dumps(kw, default=str) + "\n")


def broker() -> AlpacaBrokerProvider:
    key = os.environ.get("ALPACA_BETA_API_KEY", "")
    sec = os.environ.get("ALPACA_BETA_SECRET_KEY", "")
    assert_paper_broker_url(BASE)
    return AlpacaBrokerProvider(api_key=key, secret_key=sec, paper=True, base_url=BASE)


async def quote(sym: str) -> float:
    """Executable-side price for a long: latest BID (last trade can be stale for hours on
    thin crypto pairs). Returns 0 if the quote is older than 120 s -> watcher alerts and
    keeps the broker backup stop."""
    from datetime import datetime, timezone

    b = broker()
    async with httpx.AsyncClient(timeout=15) as c:
        if "/" in sym:
            r = await c.get(f"{DATA}/v1beta3/crypto/us/latest/quotes", params={"symbols": sym}, headers=b._headers())
            q = r.json()["quotes"][sym]
        else:
            r = await c.get(f"{DATA}/v2/stocks/{sym}/quotes/latest", params={"feed": "iex"}, headers=b._headers())
            q = r.json()["quote"]
    t = datetime.fromisoformat(str(q["t"]).replace("Z", "+00:00")[:26] + "+00:00" if "." in str(q["t"]) else str(q["t"]).replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - t).total_seconds() > 120:
        return 0.0
    return float(q.get("bp") or 0)


async def main() -> None:
    os.environ.setdefault("CLIENT_STOP_WATCH_ENABLED", "true")
    b = broker()
    await assert_paper(b)
    svc = StopWatchService(
        b, quote, alert=lambda k, d: log(alert=k, **d),
        only_symbols={x for x in os.environ.get("STOP_WATCH_SYMBOLS", "").split(",") if x} or None,
        state_path=LOGDIR / "state.json", heartbeat_path=LOGDIR / "heartbeat.json",
    )
    interval = int(os.environ.get("CLIENT_STOP_WATCH_INTERVAL_S", "15"))
    log(event="start", pid=os.getpid(), base=BASE)
    while True:
        try:
            await watchdog_check(svc)  # on startup / after a stall: verify broker stops
            rep = await svc.tick()
            log(event="tick", **rep)
        except Exception as exc:
            log(event="error", error=f"{type(exc).__name__}: {exc}")
            if type(exc).__name__ == "NotPaperError":
                raise
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
