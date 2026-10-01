#!/usr/bin/env python3
"""Crawl the Monarch terminal + Multi-Asset UI (Playwright).

Does NOT click live trading / kill-switch / autopilot / execute.
Records HTTP statuses, console errors, and screenshots.

Usage:
  python3 scripts/ui_audit.py --base https://accionesbusqueda.fastapicloud.dev \\
      --token-file /cursor/stores/self/desk.env --out /tmp/ui-audit
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

DANGEROUS_IDS = {
    "btn-kill-switch",
    "btn-kill-switch-off",
    "btn-ops-autopilot",
    "btn-ops-reconcile",
    "btn-ops-lifecycle",
    "btn-alpaca-cancel-all",
    "btn-apply-proposal",
    "btn-generate-trades",
    "btn-manage-capital",
    "btn-autopilot",
    "btn-evaluate",
    "btn-logout",
    "btn-pf-submit",
}

DANGEROUS_HREF_PREFIXES = (
    "/logout",
)

SAFE_GET_APIS = (
    "/health",
    "/api/v1/auth/me",
    "/api/v1/dashboard",
    "/api/v1/ops/status",
    "/api/v1/broker/status",
    "/api/v1/beta/multiasset/desks",
    "/api/v1/beta/multiasset/gold/status",
    "/api/v1/beta/multiasset/forex/status",
    "/api/v1/beta/multiasset/crypto/status",
    "/api/v1/beta/multiasset/history?desk=gold&limit=5",
    "/api/v1/voice/tts/status",
    "/api/v1/voice/assistant/status",
)


def _load_desk_token(path: str) -> str:
    raw = Path(path).read_text(encoding="utf-8")
    for line in raw.splitlines():
        if line.startswith("DASHBOARD_ACCESS_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("DASHBOARD_ACCESS_TOKEN missing in token file")


def _write(out: Path, name: str, data) -> None:
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    if isinstance(data, (dict, list)):
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        target.write_text(str(data), encoding="utf-8")


def main() -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://accionesbusqueda.fastapicloud.dev")
    parser.add_argument("--token-file", default="")
    parser.add_argument("--token", default=os.environ.get("DASHBOARD_ACCESS_TOKEN", ""))
    parser.add_argument("--out", default="/tmp/ui-audit")
    parser.add_argument("--company-email", default=os.environ.get("COMPANY_EMAIL", ""))
    parser.add_argument("--company-password", default=os.environ.get("COMPANY_PASSWORD", ""))
    args = parser.parse_args()
    base = args.base.rstrip("/")
    out = Path(args.out)
    token = args.token
    if args.token_file:
        token = _load_desk_token(args.token_file)
    if not token:
        raise SystemExit("Need --token or --token-file")

    report: dict = {"base": base, "roles": {}, "failures": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        _audit_role(
            browser,
            base,
            out,
            report,
            role="mesa",
            login=("desk", token),
        )
        if args.company_email and args.company_password:
            _audit_role(
                browser,
                base,
                out,
                report,
                role="empresa",
                login=("company", args.company_email, args.company_password),
            )
        else:
            report["roles"]["empresa"] = {
                "skipped": True,
                "reason": "Sin COMPANY_EMAIL/PASSWORD — Multi-Asset es solo mesa; ver tests.",
            }
        browser.close()

    _write(out, "report.json", report)
    print(json.dumps({"ok": not report["failures"], "failures": report["failures"], "out": str(out)}, indent=2))
    return 1 if report["failures"] else 0


def _audit_role(browser, base: str, out: Path, report: dict, role: str, login) -> None:
    failures = []
    role_dir = out / role
    role_dir.mkdir(parents=True, exist_ok=True)
    viewports = [("desktop", 1280, 800), ("mobile", 390, 844)]
    role_report: dict = {"viewports": {}, "apis": [], "clicks": []}

    for vp_name, w, h in viewports:
        context = browser.new_context(
            viewport={"width": w, "height": h},
            locale="es-CO",
        )
        page = context.new_page()
        page.on("dialog", lambda d: d.dismiss())
        console: list[str] = []
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None)
        http_fail: list[str] = []

        def on_response(resp):
            if resp.status >= 400:
                url = resp.url
                if "/api/" in url or url.startswith(base):
                    http_fail.append(f"{resp.status} {resp.request.method} {url}")

        page.on("response", on_response)

        # Cookie login via API so we never paint the token in the form UI logs.
        if login[0] == "desk":
            r = page.request.post(
                f"{base}/api/v1/auth/login",
                data=json.dumps({"token": login[1]}),
                headers={"Content-Type": "application/json"},
            )
        else:
            r = page.request.post(
                f"{base}/api/v1/auth/company/login",
                data=json.dumps({"email": login[1], "password": login[2]}),
                headers={"Content-Type": "application/json"},
            )
        if r.status != 200:
            failures.append(f"{role}/{vp_name} login {r.status}")
            context.close()
            continue

        # Clear localStorage to reproduce cookie-only session.
        page.goto(f"{base}/dashboard", wait_until="domcontentloaded", timeout=45000)
        page.evaluate("() => { localStorage.removeItem('nexbuy_token'); localStorage.removeItem('monarch_token'); }")
        page.reload(wait_until="domcontentloaded", timeout=45000)
        time.sleep(1.2)
        page.screenshot(path=str(role_dir / f"dashboard-{vp_name}.png"), full_page=True)
        if "/login" in page.url and "logged_out" not in page.url:
            failures.append(f"{role}/{vp_name} dashboard bounced to {page.url}")

        ma = page.goto(f"{base}/beta/multiasset", wait_until="domcontentloaded", timeout=45000)
        time.sleep(1.5)
        page.screenshot(path=str(role_dir / f"multiasset-{vp_name}.png"), full_page=True)
        title = page.locator("#desk-title")
        broker = page.locator("#desk-broker")
        if role == "mesa":
            if "/login" in page.url:
                failures.append(f"{role}/{vp_name} multiasset bounced to login (cookie-only)")
            elif title.count() and "Mesa" in (title.first.inner_text() or "") and broker.count():
                # title starts as "Mesa" then fills; empty broker after error is a fail
                txt = (broker.first.inner_text() or "").strip()
                if "Sesión expirada" in txt or "solo para la mesa" in txt:
                    failures.append(f"{role}/{vp_name} multiasset: {txt}")
            if page.locator("#universe").count() == 0 and "/dashboard" not in page.url:
                failures.append(f"{role}/{vp_name} multiasset missing #universe")
        else:
            if "/beta/multiasset" in page.url and page.locator("#universe").count():
                failures.append(f"{role}/{vp_name} empresa should not stay on Multi-Asset")

        # Click safe controls on Multi-Asset (mesa only)
        if role == "mesa" and "/beta/" in page.url:
            for sel in (".ma-tab[data-desk='forex']", ".ma-tab[data-desk='crypto']", "#btn-refresh", "#btn-history", "#btn-stats"):
                loc = page.locator(sel)
                if loc.count():
                    loc.first.click(timeout=4000)
                    time.sleep(0.4)
                    role_report["clicks"].append(sel)

        # Terminal crawl: open dashboard, click non-dangerous buttons/links
        page.goto(f"{base}/dashboard", wait_until="domcontentloaded", timeout=45000)
        time.sleep(1.0)
        if vp_name == "mobile":
            nav = page.locator("#mob-multiasset")
            if role == "mesa" and nav.count():
                nav.first.click(timeout=4000)
                time.sleep(1.0)
                page.screenshot(path=str(role_dir / "multiasset-from-mobile-nav.png"), full_page=True)
                if "/login" in page.url:
                    failures.append(f"{role}/{vp_name} mobile Multi nav bounced to login")
                page.goto(f"{base}/dashboard", wait_until="domcontentloaded", timeout=45000)

        buttons = page.locator("button, a.btn, a.mob-nav-btn")
        n = min(buttons.count(), 80)
        for i in range(n):
            el = buttons.nth(i)
            try:
                eid = el.get_attribute("id") or ""
                href = el.get_attribute("href") or ""
                if eid in DANGEROUS_IDS:
                    continue
                if any(href.startswith(p) for p in DANGEROUS_HREF_PREFIXES):
                    continue
                if href.startswith("http") and base not in href:
                    continue
                if not el.is_visible():
                    continue
                el.click(timeout=2500)
                time.sleep(0.25)
                role_report["clicks"].append(eid or href or f"idx-{i}")
            except Exception:
                continue

        # Safe GET APIs with cookie
        for path in SAFE_GET_APIS:
            resp = page.request.get(f"{base}{path}")
            role_report["apis"].append({"path": path, "status": resp.status})
            if resp.status in (401,) or resp.status >= 500:
                failures.append(f"{role} GET {path} -> {resp.status}")

        for path in (
            "/api/v1/beta/multiasset/execute",
            "/api/v1/ops/autopilot/run",
            "/api/v1/ops/kill-switch/on",
        ):
            opt = page.request.fetch(f"{base}{path}", method="OPTIONS")
            role_report["apis"].append({"path": f"OPTIONS {path}", "status": opt.status})

        page.screenshot(path=str(role_dir / f"after-crawl-{vp_name}.png"), full_page=True)
        js_errors = [c for c in console if c.startswith("error")]
        role_report["viewports"][vp_name] = {
            "url": page.url,
            "console": console[-40:],
            "http_4xx": http_fail[-40:],
            "js_errors": js_errors,
        }
        if js_errors:
            # Filter noisy third-party CDN; keep app errors
            app_err = [e for e in js_errors if "multiasset" in e.lower() or "app.js" in e.lower() or "Sesión" in e]
            for e in app_err:
                failures.append(f"{role}/{vp_name} console {e}")
        context.close()

    role_report["failures"] = [f for f in failures if f.startswith(role) or f.startswith(f"{role}/")]
    report["roles"][role] = role_report
    report["failures"].extend(failures)


if __name__ == "__main__":
    raise SystemExit(main())
