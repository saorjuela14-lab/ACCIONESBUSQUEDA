"""Per-agent and desk decision-effectiveness models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentEffectivenessRow(BaseModel):
    agent_name: str
    label_es: str
    samples: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate_pct: float | None = None
    avg_score_when_right: float | None = None
    avg_score_when_wrong: float | None = None
    committee_align_pct: float | None = None  # share of calls aligning with thesis was_correct
    current_weight: float | None = None
    stored_accuracy: float | None = None  # last recalibration snapshot (not cumulative)


class DeskEffectivenessSummary(BaseModel):
    as_of: datetime = Field(default_factory=utc_now)
    window_days: int = 1
    score_threshold: float = 5.0
    theses_evaluated: int = 0
    theses_correct: int = 0
    desk_hit_rate_pct: float | None = None
    theses_pending: int = 0
    agents: list[AgentEffectivenessRow] = Field(default_factory=list)
    best_agent: str | None = None
    weakest_agent: str | None = None
    method: str = (
        "Por cada trade cerrado: el P&L real puntúa a cada miembro (alcista si score > umbral). "
    "Las tesis no ejecutadas se evalúan al cierre de sesión (~5h / 1.5%). "
    "La mesa = % de tesis/cierres con was_correct."
    )
    disclaimer: str = (
        "N pequeño o SQLite efímero → métricas frágiles. "
        "Los miembros se puntúan al cerrar cada trade (P&L real) y a diario en tesis. "
        "Requiere DATABASE_URL persistente (Neon). No es garantía de edge futuro."
    )
    durable_db: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
