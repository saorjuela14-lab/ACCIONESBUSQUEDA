"""Read-only Strategy A eligibility gate. Never recomputes the OOS backtest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "multiasset" / "strategy_a_eligibility.json"


class EligibilityClosed(RuntimeError):
    """Strategy A must not trade — file missing or approved list empty."""


def eligibility_path(override: str | None = None) -> Path:
    if override:
        return Path(override)
    try:
        from config.settings import get_settings

        raw = str(getattr(get_settings(), "crypto_strategy_a_eligibility_path", "") or "").strip()
        if raw:
            return Path(raw)
    except Exception:
        pass
    return DEFAULT_PATH


def load_eligibility(path: Path | None = None) -> dict[str, Any]:
    p = path or eligibility_path()
    if not p.is_file():
        raise EligibilityClosed(f"eligibility_missing:{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EligibilityClosed(f"eligibility_invalid_json:{p}") from exc
    if not isinstance(data, dict):
        raise EligibilityClosed("eligibility_not_object")
    approved = data.get("approved") or []
    if not isinstance(approved, list) or not approved:
        raise EligibilityClosed("eligibility_empty")
    return data


def approved_rows(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = data if data is not None else load_eligibility()
    out: list[dict[str, Any]] = []
    for row in payload.get("approved") or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper().replace(" ", "")
        if not sym:
            continue
        if "/" not in sym and sym.endswith("USD") and len(sym) > 3:
            sym = f"{sym[:-3]}/USD"
        item = dict(row)
        item["symbol"] = sym
        out.append(item)
    if not out:
        raise EligibilityClosed("eligibility_empty")
    return out


def approved_symbols(data: dict[str, Any] | None = None) -> list[str]:
    return [r["symbol"] for r in approved_rows(data)]


def median_spread_bps(symbol: str, data: dict[str, Any] | None = None) -> float | None:
    want = symbol.upper().replace(" ", "")
    if "/" not in want and want.endswith("USD"):
        want = f"{want[:-3]}/USD"
    for row in approved_rows(data):
        if row["symbol"] == want:
            v = row.get("median_spread_bps")
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
    return None


def public_payload(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sanitized read-only view for the desk endpoint (no secrets)."""
    payload = data if data is not None else load_eligibility()
    rows = approved_rows(payload)
    return {
        "paper": True,
        "strategy": payload.get("strategy") or "A",
        "version": payload.get("version"),
        "runtime_must_not_recompute": True,
        "selection_period": payload.get("selection_period"),
        "validation_period": payload.get("validation_period"),
        "multiple_testing_correction": payload.get("multiple_testing_correction"),
        "survivorship_bias": payload.get("survivorship_bias"),
        "costs": payload.get("costs"),
        "note": payload.get("note"),
        "count": len(rows),
        "approved": [
            {
                "symbol": r.get("symbol"),
                "expectancy": r.get("expectancy"),
                "n_trades": r.get("n_trades") or r.get("trades"),
                "median_spread_bps": r.get("median_spread_bps"),
                "status": r.get("status"),
            }
            for r in rows
        ],
    }
