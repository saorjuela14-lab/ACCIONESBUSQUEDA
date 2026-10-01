"""UI smoke: Multi-Asset cookie session + crawl of terminal routes/APIs.

Does not POST live trading, kill-switch, or autopilot. Mutating endpoints
are probed with OPTIONS/GET only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

# Document routes a Mesa session must be able to load.
PAGE_ROUTES = (
    "/login",
    "/dashboard",
    "/beta/multiasset",
    "/sw.js",
    "/dashboard/sw.js",
    "/dashboard/static/styles.css",
    "/dashboard/static/app.js",
    "/dashboard/static/multiasset.js",
    "/dashboard/static/multiasset.css",
    "/dashboard/static/voice.js",
    "/dashboard/static/reliability.js",
    "/dashboard/static/manifest.json",
)

# GET APIs the terminal/Multi-Asset/Viernes UI actually reads.
SAFE_GET_APIS = (
    "/health",
    "/metrics",
    "/api/v1/auth/status",
    "/api/v1/auth/me",
    "/api/v1/dashboard",
    "/api/v1/ops/status",
    "/api/v1/broker/status",
    "/api/v1/risk/status",
    "/api/v1/alerts/push-status",
    "/api/v1/recommendations/daily/latest",
    "/api/v1/reports/daily/latest",
    "/api/v1/ops/track-record?window_days=90",
    "/api/v1/ops/month-report?window_days=30",
    "/api/v1/ops/agent-effectiveness?window_days=30",
    "/api/v1/ops/journal?limit=10&status=all&days=30",
    "/api/v1/ops/audit?limit=5&offset=0",
    "/api/v1/broker/positions",
    "/api/v1/broker/orders?status=open&limit=5",
    "/api/v1/broker/doctor",
    "/api/v1/dashboard/watchlist-matrix",
    "/api/v1/dashboard/performance-history?range=1M",
    "/api/v1/auth/companies?limit=10",
    "/api/v1/auth/capital/mine",
    "/api/v1/beta/multiasset/desks",
    "/api/v1/beta/multiasset/gold/status",
    "/api/v1/beta/multiasset/forex/status",
    "/api/v1/beta/multiasset/crypto/status",
    "/api/v1/beta/multiasset/history?desk=gold&limit=5",
    "/api/v1/beta/multiasset/track-record?desk=gold&window_days=90",
    "/api/v1/beta/multiasset/board",
    "/api/v1/beta/multiasset/last-cycle",
    "/api/v1/voice/tts/status",
    "/api/v1/voice/assistant/status",
    "/api/v1/providers/status",
    "/api/v1/watchlist",
    "/api/v1/portfolios",
    "/api/v1/alerts?limit=5",
)

# Trading / autonomy — never POST in this smoke. Existence via OPTIONS.
MUTATING_OPTIONS = (
    "/api/v1/beta/multiasset/execute",
    "/api/v1/beta/multiasset/evaluate",
    "/api/v1/beta/multiasset/autopilot/run",
    "/api/v1/ops/autopilot/run",
    "/api/v1/ops/kill-switch/on",
    "/api/v1/broker/execute/pick",
)

DANGEROUS_POST = (
    "/api/v1/ops/kill-switch/on",
    "/api/v1/ops/kill-switch/off",
    "/api/v1/ops/autopilot/run",
    "/api/v1/ops/reconcile",
    "/api/v1/broker/execute/pick",
    "/api/v1/beta/multiasset/execute",
    "/api/v1/beta/multiasset/autopilot/run",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "ui-smoke.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("MULTIASSET_BETA_ENABLED", "true")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _client():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)


@pytest.mark.asyncio
async def test_cookie_only_desk_opens_multiasset_html():
    async with await _client() as client:
        login = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        assert login.status_code == 200
        assert "nexbuy_token" in (login.headers.get("set-cookie") or "").lower()

        page = await client.get("/beta/multiasset")
        assert page.status_code == 200
        assert "Multi-asset" in page.text or "multiasset" in page.text.lower()
        assert 'id="btn-refresh"' in page.text
        # Cookie-only must not bounce on empty localStorage
        assert "if (!token())" not in page.text
        js = await client.get("/dashboard/static/multiasset.js")
        assert js.status_code == 200
        assert "location.href = \"/login\"" not in js.text
        assert "Cookie httponly is enough" in js.text
        assert "credentials: \"same-origin\"" in js.text or "credentials:\"same-origin\"" in js.text

        desks = await client.get("/api/v1/beta/multiasset/desks")
        assert desks.status_code == 200
        names = {d["desk"] for d in desks.json()["desks"]}
        assert names == {"gold", "forex", "crypto"}

        gold = await client.get("/api/v1/beta/multiasset/gold/status")
        assert gold.status_code == 200
        assert gold.json().get("strategy") or gold.json().get("desk") or "equity" in gold.json()


@pytest.mark.asyncio
async def test_unauth_multiasset_redirects_to_login_with_next():
    async with await _client() as client:
        r = await client.get("/beta/multiasset")
        assert r.status_code in (302, 307)
        loc = r.headers.get("location", "")
        assert "/login" in loc
        assert "next=/beta/multiasset" in loc


@pytest.mark.asyncio
async def test_company_session_cannot_open_multiasset_page():
    async with await _client() as client:
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]
        created = await client.post(
            "/api/v1/auth/companies",
            headers={"Authorization": f"Bearer {desk_tok}"},
            json={
                "org_name": "Acme UI",
                "email": "ops-ui@acme.test",
                "password": "segura1234",
                "full_name": "Ops",
            },
        )
        assert created.status_code == 200

        # New client jar — company cookie only
        await client.post("/api/v1/auth/logout")
        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "ops-ui@acme.test", "password": "segura1234"},
        )
        assert login.status_code == 200
        user = login.json().get("user") or {}
        assert user.get("role") in ("company_admin", "viewer", "company")

        page = await client.get("/beta/multiasset")
        assert page.status_code in (302, 307)
        assert page.headers.get("location", "") == "/dashboard"

        api = await client.get("/api/v1/beta/multiasset/desks")
        assert api.status_code == 403


@pytest.mark.asyncio
async def test_login_cookie_next_returns_to_multiasset():
    async with await _client() as client:
        await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        bounced = await client.get("/login?next=/beta/multiasset")
        assert bounced.status_code in (302, 307)
        assert bounced.headers.get("location", "") == "/beta/multiasset"


@pytest.mark.asyncio
async def test_terminal_html_exposes_multiasset_entry_points():
    async with await _client() as client:
        await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        dash = await client.get("/dashboard")
        assert dash.status_code == 200
        assert 'href="/beta/multiasset"' in dash.text
        assert 'id="btn-multiasset-beta"' in dash.text
        assert 'id="mob-multiasset"' in dash.text
        assert "Viernes" in dash.text
        assert 'id="btn-voice"' in dash.text
        js = await client.get("/dashboard/static/app.js")
        assert js.status_code == 200
        assert "ensureAuth" in js.text
        assert "credentials: \"same-origin\"" in js.text


@pytest.mark.asyncio
async def test_crawl_pages_and_safe_get_apis():
    async with await _client() as client:
        await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        failures: list[str] = []

        for path in PAGE_ROUTES:
            r = await client.get(path)
            if r.status_code >= 400:
                failures.append(f"PAGE {path} -> {r.status_code}")

        for path in SAFE_GET_APIS:
            r = await client.get(path)
            # 503 = broker/keys missing in unit env; 404/422 = empty optional payload.
            if r.status_code >= 500 and r.status_code != 503:
                failures.append(f"API {path} -> {r.status_code}")
            elif r.status_code == 401:
                failures.append(f"API {path} cookie 401")

        for path in MUTATING_OPTIONS:
            r = await client.options(path)
            # Starlette may 405 if OPTIONS not registered — still proves the app is up.
            if r.status_code >= 500:
                failures.append(f"OPTIONS {path} -> {r.status_code}")

        hrefs = _local_hrefs_from_html(DASHBOARD_DIR)
        for href in hrefs:
            if href.startswith("/api/") and any(href.startswith(d) for d in DANGEROUS_POST):
                continue
            r = await client.get(href)
            if r.status_code >= 500:
                failures.append(f"HREF {href} -> {r.status_code}")

        assert not failures, "UI crawl failures:\n" + "\n".join(failures)


def _local_hrefs_from_html(root: Path) -> set[str]:
    hrefs: set[str] = set()
    pat = re.compile(r"""(?:href|src|action)=["']([^"']+)["']""", re.I)
    for html in root.glob("*.html"):
        text = html.read_text(encoding="utf-8")
        for raw in pat.findall(text):
            if raw.startswith("#") or raw.startswith("javascript:"):
                continue
            if raw.startswith("http") or raw.startswith("mailto:"):
                continue
            path = raw.split("?")[0].split("#")[0]
            if path.startswith("/"):
                hrefs.add(path)
    return hrefs


def test_multiasset_js_never_bounces_on_empty_localstorage():
    js = (DASHBOARD_DIR / "multiasset.js").read_text(encoding="utf-8")
    assert "if (!token())" not in js
    assert "Cookie httponly is enough" in js
    assert "credentials" in js and "same-origin" in js
    assert 'location.href = "/login?next=/beta/multiasset"' in js
    # 401 is the only bounce; empty localStorage is not
    assert js.count('location.href = "/login') == 1
