"""Conversational voice assistant (Viernes) — OpenAI tools + ElevenLabs persona."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from domain.voice import VoiceChatMessage, VoiceChatResult, VoiceCommandResult
from services.cursor_agent_service import CursorAgentError, CursorAgentService
from services.voice_change_service import VoiceChangeService
from services.voice_command_service import VoiceCommandService
from utils.logging import get_logger

logger = get_logger(__name__)

_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_TTL_S = 3600.0


def _session(session_id: str) -> dict[str, Any]:
    sid = (session_id or "").strip() or str(uuid.uuid4())
    now = time.time()
    # GC
    dead = [k for k, v in _SESSIONS.items() if now - float(v.get("updated_at", 0)) > _SESSION_TTL_S]
    for k in dead:
        _SESSIONS.pop(k, None)
    if sid not in _SESSIONS:
        _SESSIONS[sid] = {"messages": [], "updated_at": now}
    _SESSIONS[sid]["updated_at"] = now
    return _SESSIONS[sid]


class VoiceAssistantService:
    """Natural desk assistant that addresses the user as boss (jefe)."""

    def __init__(self) -> None:
        self._commands = VoiceCommandService()
        self._changes = VoiceChangeService()
        self._cursor = CursorAgentService()

    def status(self) -> dict:
        settings = get_settings()
        cursor = self._cursor.status()
        return {
            "chat_enabled": settings.voice_chat_enabled,
            "openai_configured": bool(settings.openai_api_key),
            "assistant_name": settings.voice_assistant_name,
            "boss_title": settings.voice_boss_title,
            "model": settings.openai_model,
            "github_issues": bool(settings.github_token and settings.github_repo),
            "cursor_agent": cursor,
            "cursor_configured": bool(cursor.get("configured")),
        }

    async def chat(
        self,
        text: str,
        db: AsyncSession,
        *,
        portfolio_id: str | None = None,
        session_id: str | None = None,
        org_id: str | None = None,
    ) -> VoiceChatResult:
        settings = get_settings()
        raw = (text or "").strip()
        name = settings.voice_assistant_name
        boss = settings.voice_boss_title
        sid = (session_id or "").strip() or str(uuid.uuid4())
        self._org_id = org_id or "monarch"

        if not raw:
            return VoiceChatResult(
                speech=f"No te escuché, {boss}. ¿Me lo repites?",
                success=False,
                assistant_name=name,
                session_id=sid,
            )

        # Fast path: short trading commands still go through the deterministic router
        # when they clearly match (keeps buy/sell confirmations solid).
        if self._looks_like_trade_command(raw):
            cmd = await self._commands.handle(
                raw, db, portfolio_id=portfolio_id, org_id=self._org_id
            )
            speech = self._with_persona(cmd.speech, boss=boss)
            return VoiceChatResult(
                speech=speech,
                success=cmd.success,
                mode="command",
                assistant_name=name,
                ui_action=cmd.ui_action,
                ui_actions=[cmd.ui_action] if cmd.ui_action else [],
                requires_confirmation=cmd.requires_confirmation,
                pending_action=cmd.pending_action,
                tools_used=[cmd.intent],
                data=cmd.data,
                session_id=sid,
                messages=[
                    VoiceChatMessage(role="user", content=raw),
                    VoiceChatMessage(role="assistant", content=speech),
                ],
            )

        # Without OpenAI: still allow Cursor Agent launches for product/code asks
        if (not settings.openai_api_key or not settings.voice_chat_enabled) and self._looks_like_change_request(raw):
            if self._cursor.configured():
                try:
                    launched = await self._cursor.launch(
                        prompt=raw,
                        title=raw[:80],
                        area="product",
                    )
                    speech = (
                        f"{boss}, ya lancé un agente de Cursor para eso. "
                        f"Lo sigues en {launched.get('url') or 'cursor.com/agents'}."
                    )
                    return VoiceChatResult(
                        speech=speech,
                        success=True,
                        mode="cursor_agent",
                        assistant_name=name,
                        tools_used=["launch_cursor_agent"],
                        data=launched,
                        session_id=sid,
                        messages=[
                            VoiceChatMessage(role="user", content=raw),
                            VoiceChatMessage(role="assistant", content=speech),
                        ],
                    )
                except CursorAgentError as exc:
                    speech = f"{boss}, no pude lanzar Cursor: {exc}"
                    return VoiceChatResult(
                        speech=speech,
                        success=False,
                        mode="cursor_agent",
                        assistant_name=name,
                        session_id=sid,
                        messages=[
                            VoiceChatMessage(role="user", content=raw),
                            VoiceChatMessage(role="assistant", content=speech),
                        ],
                    )

        if not settings.openai_api_key or not settings.voice_chat_enabled:
            # Fallback: command router + honest note about chat needing OpenAI
            cmd = await self._commands.handle(
                raw, db, portfolio_id=portfolio_id, org_id=self._org_id
            )
            if cmd.intent != "unknown":
                speech = self._with_persona(cmd.speech, boss=boss)
                return VoiceChatResult(
                    speech=speech,
                    success=cmd.success,
                    mode="command",
                    assistant_name=name,
                    ui_action=cmd.ui_action,
                    ui_actions=[cmd.ui_action] if cmd.ui_action else [],
                    requires_confirmation=cmd.requires_confirmation,
                    pending_action=cmd.pending_action,
                    tools_used=[cmd.intent],
                    data=cmd.data,
                    session_id=sid,
                )
            cursor_note = (
                " Para cambios de página o código di algo como: "
                "cambia el texto del depósito, y lanzo un agente de Cursor."
                if self._cursor.configured()
                else ""
            )
            speech = (
                f"{boss}, el chat largo necesita OPENAI_API_KEY. "
                f"Mientras tanto puedo precios, análisis, compras con confirmación, "
                f"portafolio y watchlist.{cursor_note}"
            )
            return VoiceChatResult(
                speech=speech,
                success=False,
                mode="fallback",
                assistant_name=name,
                session_id=sid,
            )

        return await self._llm_chat(raw, db, portfolio_id=portfolio_id, session_id=sid)

    def _looks_like_trade_command(self, text: str) -> bool:
        t = text.lower()
        keys = (
            "compra ", "vende ", "vender ", "confirma", "confirmá", "cancel",
            "cancela", "ejecuta la orden",
        )
        return any(k in t for k in keys)

    def _looks_like_change_request(self, text: str) -> bool:
        t = text.lower()
        keys = (
            "cambia ", "cambiar ", "arregla ", "arreglar ", "implementa ",
            "implementar ", "modifica ", "modificar ", "rediseña", "rediseñar",
            "haz este cambio", "haz un cambio", "actualiza la página",
            "actualiza el", "corrige ", "añade ", "agrega ", "quita el texto",
            "elimina el texto", "en el código", "en la página", "en el dashboard",
            "lanza un agente", "agente de cursor", "cursor agent",
        )
        return any(k in t for k in keys)

    def _with_persona(self, speech: str, *, boss: str) -> str:
        s = (speech or "").strip()
        if not s:
            return f"Listo, {boss}."
        low = s.lower()
        if boss.lower() in low or "jefe" in low:
            return s
        # Light touch — don't force on every line
        if s.endswith("?") or s.endswith("."):
            return s
        return f"{s} ¿Algo más, {boss}?"

    def _system_prompt(self) -> str:
        settings = get_settings()
        name = settings.voice_assistant_name
        boss = settings.voice_boss_title
        cursor_on = self._cursor.configured()
        cursor_line = (
            "Para cambios de UI/código/producto usa launch_cursor_agent (o queue_product_change, "
            "que también lanza Cursor si está configurado). Eso dispara un Cloud Agent real en el repo "
            "que edita código y puede abrir PR. Di al jefe la URL del agente. "
            "No digas que ya editaste el repo tú misma: lo hace el agente de Cursor."
            if cursor_on
            else "Si piden cambios de código y Cursor no está configurado, usa queue_product_change "
            "(cola local / GitHub issue)."
        )
        return f"""Eres {name}, la asistente personal de voz de Monarch Capital (mesa de inversión autónoma).
Hablas en español latino natural, cálida y profesional — tipo secretaria ejecutiva / Friday de Iron Man.
SIEMPRE te diriges al usuario como "{boss}" (de vez en cuando "jefe", nunca "usuario").
Respuestas CORTAS para ser leídas en voz alta (2–5 frases). Sin markdown, sin viñetas, sin tablas.
Números redondeados y claros. Si vas a operar (comprar/vender), pide confirmación explícita.
Puedes: mercado, precios, análisis, portafolio/broker, watchlist, autopilot, kill switch,
simular escenarios, aconsejar asignación, y lanzar agentes de Cursor para cambios reales.
{cursor_line}
Usa cursor_agent_status / list_cursor_agents si pregunta cómo va un agente.
Trading y consultas rápidas: herramientas locales. Cambios de producto: Cursor Agent.
Si no estás segura, pregunta una sola cosa. Sé proactiva y útil."""

    def _tool_specs(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "run_voice_command",
                    "description": "Ejecuta un comando de mesa (mercado, precio, analiza, posiciones, watchlist, discovery, etc.)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Frase de comando en español"},
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_ops_status",
                    "description": "Estado del escritorio: kill switch, autopilot, firm autonomy, risk",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_autopilot",
                    "description": "Lanza un ciclo del autopilot de la firma",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_kill_switch",
                    "description": "Activa o desactiva el kill switch (frena compras)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["enabled"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "simulate_investment",
                    "description": "Simula un escenario de inversión (capital, retorno esperado, meses)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "capital": {"type": "number"},
                            "expected_return_pct": {"type": "number"},
                            "horizon_months": {"type": "integer"},
                        },
                        "required": ["capital"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "advise_allocation",
                    "description": "Sugiere asignación de capital (emerging_focused|balanced|defensive)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "capital": {"type": "number"},
                            "strategy_style": {"type": "string"},
                        },
                        "required": ["capital"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "queue_product_change",
                    "description": (
                        "Registra un cambio pedido por el jefe. Si CURSOR_API_KEY está activa, "
                        "lanza un Cloud Agent de Cursor en el repo (preferido)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "area": {
                                "type": "string",
                                "description": "strategy|ui|code|product|ops",
                            },
                        },
                        "required": ["title", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "launch_cursor_agent",
                    "description": (
                        "Lanza un Cloud Agent de Cursor para implementar un cambio en el repo "
                        "(código, UI, producto). Úsalo cuando el jefe pida cambios reales."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Instrucción completa para el agente",
                            },
                            "title": {"type": "string"},
                            "area": {"type": "string"},
                        },
                        "required": ["prompt"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cursor_agent_status",
                    "description": "Consulta estado de un agente de Cursor (y su último run)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "Id bc-... del agente",
                            },
                        },
                        "required": ["agent_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_cursor_agents",
                    "description": "Lista agentes de Cursor recientes (remotos + lanzados por Viernes)",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_change_requests",
                    "description": "Lista solicitudes de cambio recientes pedidas por voz",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def _llm_chat(
        self,
        text: str,
        db: AsyncSession,
        *,
        portfolio_id: str | None,
        session_id: str,
    ) -> VoiceChatResult:
        settings = get_settings()
        name = settings.voice_assistant_name
        boss = settings.voice_boss_title
        mem = _session(session_id)
        history: list[dict[str, Any]] = list(mem["messages"])

        history.append({"role": "user", "content": text})
        # trim
        max_h = max(4, settings.voice_chat_max_history)
        history = history[-max_h:]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            *history,
        ]

        tools_used: list[str] = []
        ui_actions: list[str] = []
        requires_confirmation = False
        pending_action = None
        data: dict[str, Any] = {}
        cmd_result: VoiceCommandResult | None = None

        speech = f"Dame un segundo, {boss}."
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                for _round in range(4):
                    payload = {
                        "model": settings.openai_model,
                        "messages": messages,
                        "tools": self._tool_specs(),
                        "tool_choice": "auto",
                        "temperature": 0.55,
                        "max_tokens": 450,
                    }
                    r = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                        json=payload,
                    )
                    if r.status_code >= 400:
                        logger.warning("voice.chat.openai_http", status=r.status_code, body=r.text[:240])
                        speech = (
                            f"{boss}, tuve un problema hablando con el modelo. "
                            f"¿Probamos un comando directo, tipo precio de NVDA?"
                        )
                        break
                    msg = r.json()["choices"][0]["message"]
                    tool_calls = msg.get("tool_calls") or []
                    if not tool_calls:
                        speech = (msg.get("content") or "").strip() or f"Listo, {boss}."
                        messages.append({"role": "assistant", "content": speech})
                        break

                    messages.append(msg)
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        fname = fn.get("name") or ""
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        tools_used.append(fname)
                        result, extra = await self._dispatch_tool(
                            fname, args, db, portfolio_id=portfolio_id
                        )
                        if extra.get("ui_action"):
                            ui_actions.append(extra["ui_action"])
                        if extra.get("requires_confirmation"):
                            requires_confirmation = True
                            pending_action = extra.get("pending_action")
                        if extra.get("data"):
                            data[fname] = extra["data"]
                        if extra.get("cmd_result"):
                            cmd_result = extra["cmd_result"]
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id"),
                                "content": json.dumps(result, ensure_ascii=False)[:3500],
                            }
                        )
                else:
                    # exhausted tool rounds — ask model once more without tools
                    r = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                        json={
                            "model": settings.openai_model,
                            "messages": messages,
                            "temperature": 0.5,
                            "max_tokens": 350,
                        },
                    )
                    if r.status_code < 400:
                        speech = (r.json()["choices"][0]["message"].get("content") or "").strip()
        except Exception as exc:
            logger.warning("voice.chat.failed", error=str(exc))
            speech = f"{boss}, se me cruzaron los cables un momento: {exc}"

        speech = speech.strip()
        if boss.lower() not in speech.lower() and "jefe" not in speech.lower():
            if not speech.endswith("?") and len(speech) < 280:
                speech = f"{speech.rstrip('.')} ¿Te ayudo en algo más, {boss}?"

        # persist history
        history.append({"role": "assistant", "content": speech})
        mem["messages"] = history[-max_h:]

        return VoiceChatResult(
            speech=speech,
            success=True,
            mode="chat",
            assistant_name=name,
            ui_action=ui_actions[0] if ui_actions else None,
            ui_actions=ui_actions,
            requires_confirmation=requires_confirmation,
            pending_action=pending_action or (cmd_result.pending_action if cmd_result else None),
            tools_used=tools_used,
            data=data or None,
            session_id=session_id,
            messages=[
                VoiceChatMessage(role="user", content=text),
                VoiceChatMessage(role="assistant", content=speech),
            ],
        )

    async def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        db: AsyncSession,
        *,
        portfolio_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra: dict[str, Any] = {}
        try:
            if name == "run_voice_command":
                cmd = await self._commands.handle(
                    str(args.get("text") or ""),
                    db,
                    portfolio_id=portfolio_id,
                    org_id=getattr(self, "_org_id", None) or "monarch",
                )
                extra["ui_action"] = cmd.ui_action
                extra["requires_confirmation"] = cmd.requires_confirmation
                extra["pending_action"] = cmd.pending_action
                extra["data"] = cmd.data
                extra["cmd_result"] = cmd
                return (
                    {
                        "intent": cmd.intent,
                        "success": cmd.success,
                        "speech": cmd.speech,
                        "requires_confirmation": cmd.requires_confirmation,
                    },
                    extra,
                )

            if name == "get_ops_status":
                from config.settings import get_settings as _gs
                from database.repositories.ops_repository import OpsFlagRepository
                from services.auto_execute_service import AutoExecuteService
                from services.kill_switch_service import KillSwitchService

                settings = _gs()
                ks = await KillSwitchService(db).status()
                auto = AutoExecuteService(db)
                ok, reason = await auto.can_auto_trade_async()
                promo = await OpsFlagRepository(db).get_json("paper_promotion")
                status = {
                    "kill_switch": ks.model_dump(mode="json"),
                    "firm_autonomy": settings.firm_autonomy,
                    "autopilot_interval_minutes": settings.effective_autopilot_interval_minutes,
                    "auto_execute_allowed": ok,
                    "auto_execute_reason": reason,
                    "paper_promotion": promo or {"promoted": False},
                }
                return {"status": status}, extra

            if name == "run_autopilot":
                from services.autopilot_service import AutopilotService

                report = await AutopilotService(db).run(
                    session_label="voice",
                    actor="voice_viernes",
                )
                payload = report if isinstance(report, dict) else {"result": str(report)}
                # Keep speech payload small
                slim = {
                    k: payload.get(k)
                    for k in ("started_at", "aborted", "reconcile", "picks", "execute", "lifecycle")
                    if k in payload
                } or {"keys": list(payload.keys())[:12]}
                return {"ok": True, "report": slim}, extra

            if name == "set_kill_switch":
                from services.kill_switch_service import KillSwitchService

                enabled = bool(args.get("enabled"))
                reason = str(args.get("reason") or "Solicitado por voz")
                svc = KillSwitchService(db)
                if enabled:
                    # Voice path: arm switch without flattening the book (safer)
                    state = await svc.activate(
                        reason=reason,
                        actor="voice_viernes",
                        flatten=False,
                        confirm=True,
                    )
                else:
                    state = await svc.deactivate(actor="voice_viernes", confirm=True)
                payload = state.model_dump(mode="json")
                return {"ok": True, "kill_switch": payload}, extra

            if name == "simulate_investment":
                capital = float(args.get("capital") or 0)
                ret = float(args.get("expected_return_pct") or 12.0)
                months = int(args.get("horizon_months") or 12)
                if capital <= 0:
                    return {"ok": False, "error": "capital inválido"}, extra
                # Illustrative projection (spoken-friendly); not a formal backtest
                annual = ret / 100.0
                future = capital * ((1 + annual) ** (months / 12.0))
                conservative = capital * ((1 + annual * 0.5) ** (months / 12.0))
                adverse = capital * ((1 + max(annual * -0.4, -0.35)) ** (months / 12.0))
                return {
                    "ok": True,
                    "capital": capital,
                    "expected_return_pct": ret,
                    "horizon_months": months,
                    "base_value": round(future, 2),
                    "conservative_value": round(conservative, 2),
                    "adverse_value": round(adverse, 2),
                    "note": "Proyección ilustrativa, no promesa de retorno",
                }, extra

            if name == "advise_allocation":
                import asyncio

                from database.repositories.investment_memory_repository import InvestmentMemoryRepository
                from database.repositories.watchlist_repository import WatchlistRepository
                from providers.market.factory import get_market_provider
                from services.market_allocation_advisor_service import MarketAllocationAdvisorService
                from services.market_dashboard_service import MarketDashboardService

                capital = float(args.get("capital") or 0)
                style = str(args.get("strategy_style") or "balanced")
                watchlist = await WatchlistRepository(db).list_active(
                    org_id=getattr(self, "_org_id", None) or "monarch"
                )
                memory = await InvestmentMemoryRepository(db).latest_by_ticker(
                    [w.ticker for w in watchlist]
                )
                dash = MarketDashboardService()
                indices, sectors, _, _ = await asyncio.gather(
                    dash._fetch_indices(),
                    dash._fetch_sector_heatmap(),
                    dash._economic_calendar(),
                    dash._market_news(),
                )
                regime, regime_score = dash._compute_market_regime(indices, sectors)
                strong_sectors = [s.sector for s in sectors if s.regime == "bullish"][:5]
                plan = await MarketAllocationAdvisorService(get_market_provider()).advise(
                    capital=capital,
                    watchlist=watchlist,
                    memory_by_ticker=memory,
                    market_regime=regime,
                    market_regime_score=regime_score,
                    strategy_style=style,
                    strong_sectors=strong_sectors,
                )
                payload = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
                allocs = payload.get("allocations") or payload.get("recommended") or []
                summary = {
                    "summary": payload.get("summary"),
                    "market_view": payload.get("market_view"),
                    "cash_reserve_pct": payload.get("cash_reserve_pct"),
                    "lines": [
                        {
                            "ticker": a.get("ticker") if isinstance(a, dict) else getattr(a, "ticker", None),
                            "allocation_usd": (
                                a.get("allocation_usd")
                                if isinstance(a, dict)
                                else getattr(a, "allocation_usd", None)
                            ),
                        }
                        for a in allocs[:6]
                    ],
                }
                return {"ok": True, "plan": summary}, {"data": summary}

            if name == "queue_product_change":
                item = await self._changes.queue_change(
                    title=str(args.get("title") or "Cambio"),
                    description=str(args.get("description") or ""),
                    area=str(args.get("area") or "product"),
                )
                return {"ok": True, "request": item}, {"data": item}

            if name == "launch_cursor_agent":
                launched = await self._cursor.launch(
                    prompt=str(args.get("prompt") or ""),
                    title=str(args.get("title") or ""),
                    area=str(args.get("area") or "product"),
                )
                return launched, {"data": launched}

            if name == "cursor_agent_status":
                info = await self._cursor.get_agent(str(args.get("agent_id") or ""))
                return info, {"data": info}

            if name == "list_cursor_agents":
                listed = await self._cursor.list_remote(limit=8)
                return listed, {"data": listed}

            if name == "list_change_requests":
                items = self._changes.list_recent()
                return {"ok": True, "requests": items}, {"data": {"requests": items}}

            return {"ok": False, "error": f"herramienta desconocida: {name}"}, extra
        except Exception as exc:
            logger.warning("voice.tool.failed", tool=name, error=str(exc))
            return {"ok": False, "error": str(exc)}, extra
