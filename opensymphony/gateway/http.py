"""HTTP Gateway — FastAPI REST interface for OpenSymphony."""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("symphony.gateway")


# ── P0-3: API Key authentication ──────────────────────────────────

_api_key: str | None = None


def _get_api_key() -> str | None:
    """Get API key from environment. Returns None if not set (no auth)."""
    global _api_key
    if _api_key is None and not hasattr(_get_api_key, '_checked'):
        _get_api_key._checked = True
        _api_key = os.environ.get("SYMPHONY_API_KEY", "") or None
    return _api_key


def _check_auth(request: Any) -> None:
    """Check API key authentication. Raises HTTPException if auth fails."""
    key = _get_api_key()
    if key is None:
        return  # No key configured, skip auth

    from fastapi import HTTPException

    # Check Authorization: Bearer <key>
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        if auth_header[7:] == key:
            return

    # Check X-API-Key: <key>
    api_key_header = request.headers.get("x-api-key", "")
    if api_key_header == key:
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


# ── P1-6: Simple in-memory rate limiter ───────────────────────────

_rate_limit_store: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 10  # requests per window


def _check_rate_limit(user_id: str) -> None:
    """Check rate limit for user. Raises HTTPException if exceeded."""
    from fastapi import HTTPException

    now = time.time()
    if user_id not in _rate_limit_store:
        _rate_limit_store[user_id] = []

    # Clean old entries
    _rate_limit_store[user_id] = [
        t for t in _rate_limit_store[user_id] if now - t < _RATE_LIMIT_WINDOW
    ]

    if len(_rate_limit_store[user_id]) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
        )

    _rate_limit_store[user_id].append(now)


class ChatRequest(BaseModel):
    message: str
    soul_id: str | None = None
    agent_id: str | None = None
    task_type: str = "chat"
    max_tokens: int = 4096
    temperature: float = 0.7
    tools: list[str] | None = None  # tool names to enable for function calling


class ChatResponse(BaseModel):
    agent_id: str
    soul_name: str
    response: str
    model: str
    latency_ms: float


def create_app(kernel: Any):
    """Create FastAPI app bound to a Symphony kernel."""
    from fastapi import FastAPI, HTTPException, Request

    app = FastAPI(
        title="Symphony Framework",
        version="0.1.0",
        description="Multi-agent framework with governance, soul, and self-evolution",
    )

    # Bridge handler
    bridge_handler = None
    try:
        from opensymphony.gateway.bridge import BridgeHandler
        bridge_handler = BridgeHandler(kernel)
    except ImportError as e:
        logger.warning(f"Bridge not available: {e}")

    # P0-3: Auth middleware
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        try:
            _check_auth(request)
        except Exception:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        response = await call_next(request)
        return response

    @app.get("/health")
    async def health():
        return kernel.health()

    @app.get("/souls")
    async def list_souls():
        return kernel.list_souls()

    @app.get("/agents")
    async def list_agents():
        return kernel.list_agents()

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        if req.agent_id:
            agent = kernel.get_agent(req.agent_id)
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent {req.agent_id} not found")
        else:
            agent = kernel.create_agent(soul_id=req.soul_id, metadata={"task_type": req.task_type})

        try:
            if req.tools:
                # Native function calling path
                result = agent.chat_with_fc(
                    req.message, tool_names=req.tools,
                    max_tokens=req.max_tokens, temperature=req.temperature,
                )
                soul_name = agent.soul.name if agent.soul else agent.id
                return ChatResponse(
                    agent_id=agent.id, soul_name=soul_name,
                    response=result["answer"],
                    model="fc-loop",
                    latency_ms=0.0,
                )
            else:
                response = agent.chat(
                    req.message,
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                )
        except Exception as e:
            # P0-6: Don't leak internal errors
            logger.error(f"Chat error: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="An error occurred processing your request")

        soul_name = agent.soul.name if agent.soul else agent.id

        return ChatResponse(
            agent_id=agent.id,
            soul_name=soul_name,
            response=response.content,
            model=f"{response.provider}/{response.model}",
            latency_ms=response.latency_ms,
        )

    # ── Human Chat endpoint (v0.3) ───────────────────────────────
    class HumanChatRequest(BaseModel):
        message: str
        user_id: str
        target_agent: str | None = None

    @app.post("/human/chat")
    async def human_chat(req: HumanChatRequest, request: Request):
        """Human-facing chat endpoint. Uses IntentBridge + Soul human mode."""
        # P1-6: Rate limit
        _check_rate_limit(req.user_id)
        try:
            result = await kernel.handle_human_message(
                user_id=req.user_id,
                message=req.message,
                target_agent=req.target_agent,
            )
            return result
        except Exception as e:
            # P0-6: Don't leak internal errors
            logger.error(f"Human chat error: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="An error occurred processing your request")

    @app.delete("/agents/{agent_id}")
    async def terminate_agent(agent_id: str):
        agent = kernel.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        from ..agents.agent import AgentStatus
        agent.status = AgentStatus.TERMINATED
        return {"status": "terminated", "agent_id": agent_id}

    @app.post("/agents/{agent_id}/handoff")
    async def agent_handoff(agent_id: str, params: dict):
        """Agent-initiated handoff to another agent (governance-checked)."""
        agent = kernel.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        target_soul = params.get("target_soul")
        if not target_soul:
            raise HTTPException(status_code=400, detail="target_soul is required")
        context = params.get("context", {})
        try:
            result = agent.handoff(target_soul=target_soul, context=context, kernel=kernel)
        except Exception as e:
            logger.error(f"Handoff error: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="An error occurred processing your request")
        resp = {"status": result.status, "reason": result.reason}
        if result.target_agent:
            resp["target_agent_id"] = result.target_agent.id
            resp["target_soul"] = target_soul
        if result.governance:
            resp["risk_level"] = result.governance.risk_level.value
        return resp

    # Governance endpoints
    @app.get("/governance/health")
    async def governance_health():
        if not kernel._governance:
            raise HTTPException(status_code=501, detail="Governance not initialized")
        return kernel._governance.health()

    @app.get("/governance/hitl/pending")
    async def hitl_pending():
        if not kernel._governance:
            raise HTTPException(status_code=501, detail="Governance not initialized")
        return kernel._governance.hitl.list_pending()

    @app.post("/governance/hitl/{request_id}/approve")
    async def hitl_approve(request_id: str):
        if not kernel._governance:
            raise HTTPException(status_code=501, detail="Governance not initialized")
        result = kernel._governance.hitl.approve(request_id)
        if not result:
            raise HTTPException(status_code=404, detail="Request not found")
        return {"status": "approved", "request_id": request_id}

    @app.post("/governance/hitl/{request_id}/reject")
    async def hitl_reject(request_id: str):
        if not kernel._governance:
            raise HTTPException(status_code=501, detail="Governance not initialized")
        result = kernel._governance.hitl.reject(request_id)
        if not result:
            raise HTTPException(status_code=404, detail="Request not found")
        return {"status": "rejected", "request_id": request_id}

    # ── Telemetry & Archive endpoints ────────────────────────────────
    @app.get("/telemetry/summary")
    async def telemetry_summary(date: str = ""):
        return kernel._telemetry.get_daily_summary(date)

    @app.get("/telemetry/candidates")
    async def telemetry_candidates():
        return {"candidates": kernel._telemetry.get_skill_candidates()}

    @app.get("/telemetry/stats")
    async def telemetry_stats():
        return kernel._telemetry.get_total_records()

    @app.post("/archive/daily")
    async def archive_daily():
        from ..archive import ArchiveEngine
        ae = ArchiveEngine(db_path=kernel.data_dir / "telemetry.db",
                           archive_path=kernel.data_dir / "archive")
        return ae.run_daily_archive()

    @app.post("/archive/weekly")
    async def archive_weekly():
        from ..archive import ArchiveEngine
        ae = ArchiveEngine(db_path=kernel.data_dir / "telemetry.db",
                           archive_path=kernel.data_dir / "archive")
        return ae.run_weekly_rollup()

    @app.get("/archive/list")
    async def archive_list():
        from ..archive import ArchiveEngine
        ae = ArchiveEngine(db_path=kernel.data_dir / "telemetry.db",
                           archive_path=kernel.data_dir / "archive")
        return ae.list_archives()

    # ── Skill Management endpoints ──────────────────────────────────
    @app.get("/skills")
    async def skills_list():
        return {"skills": kernel._skill_registry.list_all()}

    @app.get("/skills/candidates")
    async def skills_candidates():
        candidates = kernel._skill_registry.get_candidates()
        return {"candidates": [vars(c) for c in candidates]}

    @app.post("/skills/{skill_id}/approve")
    async def skills_approve(skill_id: str):
        ok = kernel._skill_registry.approve(skill_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Skill not found or not a candidate")
        return {"status": "approved", "skill_id": skill_id}

    @app.post("/skills/{skill_id}/reject")
    async def skills_reject(skill_id: str):
        ok = kernel._skill_registry.reject(skill_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Skill not found or not a candidate")
        return {"status": "rejected", "skill_id": skill_id}

    @app.post("/skills/create")
    async def skills_create(params: dict):
        from ..skill_registry import Skill
        skill = Skill(
            trigger=params.get("trigger", ""),
            pattern=params.get("pattern", ""),
            fix_type=params.get("fix_type", "P2_param"),
            suggestion=params.get("suggestion", ""),
            pre_action=params.get("pre_action", ""),
            post_action=params.get("post_action", ""),
            confidence=params.get("confidence", 0.5),
            evidence=params.get("evidence", []),
            status=params.get("status", "candidate"),
        )
        sid = kernel._skill_registry.add(skill)
        return {"status": "created", "skill_id": sid}

    @app.get("/skills/stale")
    async def skills_stale(days: int = 30):
        stale = kernel._skill_registry.get_stale_skills(days)
        return {"stale": [{"id": s.id, "trigger": s.trigger, "last_triggered": s.last_triggered} for s in stale]}

    # ── Production Tools endpoints ──────────────────────────────────
    try:
        from ..tools.production import call_tool, register_all
        from ..tools.production import list_tools as _list_tools
        register_all()

        @app.get("/tools")
        async def tools_list():
            return {"tools": _list_tools()}

        @app.post("/tools/{tool_name}")
        async def tools_execute(tool_name: str, params: dict = None):
            result = call_tool(tool_name, params or {})
            return result
    except Exception as e:
        logger.warning(f"Production tools not loaded: {e}")

    # ── Pipeline endpoints ──────────────────────────────────────────
    @app.post("/pipeline/run")
    async def pipeline_run(params: dict):
        """Run a declarative pipeline."""
        from ..pipeline import Pipeline, PipelineStep
        steps_data = params.get("steps", [])
        context = params.get("context", {})
        if not steps_data:
            raise HTTPException(status_code=400, detail="steps is required")
        try:
            steps = [PipelineStep(**s) for s in steps_data]
            pipe = Pipeline(steps=steps, kernel=kernel)
            result = pipe.run(context)
            result["results"] = [
                {"step_id": r.step_id, "success": r.success, "output": r.output,
                 "error": r.error, "attempts": r.attempts, "latency_ms": r.latency_ms,
                 "used_fallback": r.used_fallback}
                for r in result["results"]
            ]
            return result
        except Exception as e:
            logger.error(f"Pipeline error: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail="An error occurred processing your request")

    @app.get("/pipeline/templates")
    async def pipeline_templates():
        """List built-in pipeline templates."""
        return {"templates": {
            "story": {
                "description": "Story script → scene direction → image generation",
                "steps": [
                    {"id": "write", "soul": "screenwriter", "output_key": "script"},
                    {"id": "direct", "soul": "drama_director", "input_key": "script", "output_key": "prompts"},
                    {"id": "render", "tool": "jimeng_image", "input_key": "prompts", "output_key": "images",
                     "retry": 3, "fallback_soul": "reflector"},
                ]
            },
            "legal_article": {
                "description": "Legal article writing with review",
                "steps": [
                    {"id": "draft", "soul": "legal_writer", "output_key": "draft"},
                    {"id": "review", "tool": "legal_review", "input_key": "draft", "output_key": "reviewed"},
                    {"id": "quality", "tool": "quality_check", "input_key": "reviewed", "output_key": "result"},
                ]
            },
            "text_only": {
                "description": "Simple text generation with a soul",
                "steps": [
                    {"id": "generate", "soul": "default", "output_key": "text"},
                ]
            }
        }}

    # WebSocket Bridge endpoint
    if bridge_handler:
        @app.websocket_route("/bridge")
        async def websocket_bridge(websocket):
            conn_id = ""
            try:
                await websocket.accept()
                conn_id = uuid.uuid4().hex[:8]
                welcome = json.dumps({
                    "id": uuid.uuid4().hex[:12], "source": "symphony",
                    "target": "client", "type": "event",
                    "action": "connected",
                    "payload": {"conn_id": conn_id, "version": "0.1.0"},
                }, ensure_ascii=False)
                await websocket.send_text(welcome)
                bridge_handler._connections[conn_id] = websocket
                while True:
                    data = await websocket.receive_text()
                    await bridge_handler.on_message(conn_id, data)
            except Exception as e:
                logger.error(f"Bridge error: {e}\n{traceback.format_exc()}")
                bridge_handler._connections.pop(conn_id, None)

    return app
