"""
JARVIS FastAPI backend — Phase 7 web interface.

Provides REST + SSE endpoints for:
- Chat (with SSE streaming)
- Conversation management
- Provider listing and switching
- Task management
- Voice STT/TTS
- Health check
- Infrastructure status

All Phase 5 security controls remain enforced through the existing Agent.
"""
import asyncio
import json
import logging
import uuid
import time
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from app.config import Config
from app.agent import Agent, SYSTEM_PROMPT
from app.llm_router import build_router, LLMRouter, ProviderState
from app.tool_registry import TOOL_SCHEMAS, TOOL_NAMES, execute_tool, handle_memory_command
from app.api.models import (
    ChatRequest, ChatResponse, ChatMessage, MessageRole, ToolCallInfo, ToolResultInfo,
    Conversation, ConversationSummary,
    ProviderInfo, ProviderListResponse, ProviderSwitchRequest,
    TaskInfo, TaskStatus, TaskCancelRequest, TaskListResponse,
    VoiceTranscriptionResponse, TTSRequest, TTSResponse,
    HealthResponse,
)
from app.api.conversation_store import get_store
from app.api.task_manager import get_task_manager
from app.api.voice import get_stt, get_tts
from app.control_plane import get_control_plane
from app.terminal import router as terminal_router

logger = logging.getLogger("api")

app = FastAPI(title="JARVIS API", version="phase7")
app.include_router(terminal_router)


@app.on_event("startup")
def warm_voice_models():
    """Load optional voice STT in the background; never delay API startup."""
    if Config.VOICE_ENABLED:
        import threading
        threading.Thread(target=lambda: get_stt().available, daemon=True, name="jarvis-stt-warmup").start()
        threading.Thread(target=lambda: get_tts().available, daemon=True, name="jarvis-tts-warmup").start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (terminal.js)
app.mount("/static", StaticFiles(directory="/home/abin/ai/devops-agent/app/static"), name="static")

# ── State ──────────────────────────────────────────────────────

_router: Optional[LLMRouter] = None
_infra_cache: dict = {}
_infra_cache_time: float = 0
INFRA_CACHE_TTL = 30  # seconds


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = build_router(Config)
    return _router


def _build_agent(provider_override: Optional[str] = None) -> Agent:
    """Build an agent with an optional request-scoped provider override.

    The override never mutates the router's selected provider, so normal chat
    behavior and failover policy remain unchanged.
    """
    router = get_router()
    provider = router
    if provider_override:
        wanted = provider_override.strip().lower()
        for state in router.providers:
            provider_id = state.provider.name.split("(", 1)[0].strip().lower()
            if provider_id == wanted:
                provider = state.provider
                break
        else:
            raise ValueError(f"Unknown provider: {provider_override}")
    return Agent(
        provider=provider,
        tool_schemas=TOOL_SCHEMAS,
        execute_tool_fn=execute_tool,
        memory_command_handler=handle_memory_command,
        allowed_tool_names=TOOL_NAMES,
    )


# ── Health ─────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
def health():
    router = get_router()
    return HealthResponse(
        status="ok",
        version="phase7",
        provider=router.name,
    )


# ── Infrastructure Status ─────────────────────────────────────

@app.get("/api/infra/status")
def infra_status():
    """
    Lightweight infrastructure status for the sidebar dashboard.
    Uses cached results from the last health check to avoid repeated SSH calls.
    """
    global _infra_cache, _infra_cache_time

    # Check if we have cached results
    now = time.time()
    if _infra_cache and (now - _infra_cache_time) < INFRA_CACHE_TTL:
        return {"status": "ok", "cached": True, "data": _infra_cache}

    # Run a quick health check and cache results
    try:
        from app.tool_registry import execute_tool as exec_tool
        result = exec_tool("check_infrastructure_health", {})
        if result.get("success"):
            _infra_cache = result
            _infra_cache_time = now
            return {"status": "ok", "cached": False, "data": result}
        else:
            return {"status": "error", "cached": False, "error": result.get("error", "Health check failed")}
    except Exception as e:
        logger.error("infra_status_error=%s", e)
        return {"status": "error", "cached": False, "error": str(e)[:200]}


@app.post("/api/infra/refresh")
def infra_refresh():
    """Force refresh infrastructure status."""
    global _infra_cache, _infra_cache_time
    _infra_cache = {}
    _infra_cache_time = 0
    return infra_status()


# ── Providers ──────────────────────────────────────────────────

@app.get("/api/providers", response_model=ProviderListResponse)
def list_providers():
    router = get_router()
    states = router.providers
    providers = []
    for s in states:
        p = s.provider
        available = True
        status = "available"
        if hasattr(p, 'api_key') and not getattr(p, 'api_key', ''):
            available = False
            status = "not_configured"
        if s.status == "cooldown":
            status = "cooldown"
        elif s.status == "failed":
            status = "unavailable"
        elif s.status == "rate_limited":
            status = "rate_limited"

        name = p.name
        model = ""
        if "(" in name:
            parts = name.split("(")
            provider_id = parts[0].strip().lower()
            model = parts[1].rstrip(")").split(",")[0].strip()
        else:
            provider_id = name.lower()

        providers.append(ProviderInfo(
            id=provider_id,
            name=name,
            model=model,
            available=available,
            status=status,
        ))

    return ProviderListResponse(
        providers=providers,
        active_provider=router.primary.name,
    )


@app.post("/api/providers/switch")
def switch_provider(req: ProviderSwitchRequest):
    """Switch the active provider. This does NOT reset the conversation."""
    router = get_router()
    target = req.provider.lower()
    for i, state in enumerate(router._states):
        pid = state.provider.name.split("(")[0].strip().lower() if "(" in state.provider.name else state.provider.name.lower()
        if target in pid or target == pid:
            router._primary_index = i
            return {"success": True, "provider": state.provider.name}
    raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")


# ── Conversations ──────────────────────────────────────────────

@app.get("/api/conversations", response_model=list[ConversationSummary])
def list_conversations():
    return get_store().list_all()


@app.post("/api/conversations", response_model=Conversation)
def create_conversation(title: str = "New Chat"):
    return get_store().create(title=title)


@app.get("/api/conversations/search")
def search_conversations(q: str = ""):
    if not q:
        return get_store().list_all()
    return get_store().search(q)


@app.get("/api/conversations/{conv_id}", response_model=Conversation)
def get_conversation(conv_id: str):
    conv = get_store().get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.put("/api/conversations/{conv_id}/rename")
def rename_conversation(conv_id: str, body: dict):
    title = body.get("title", "")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not get_store().rename(conv_id, title):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    if not get_store().delete(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}


@app.post("/api/conversations/{conv_id}/provider")
def set_conversation_provider(conv_id: str, body: ProviderSwitchRequest):
    if not get_store().update_provider(conv_id, body.provider):
        raise HTTPException(status_code=404, detail="Conversation not found")
    switch_provider(body)
    return {"success": True, "provider": body.provider}


# ── Chat (non-streaming) ──────────────────────────────────────

@app.post("/api/conversations/{conv_id}/chat")
async def chat(conv_id: str, req: ChatRequest):
    """Send a message and get a response (non-streaming)."""
    store = get_store()
    conv = store.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_msg = ChatMessage(
        role=MessageRole.USER,
        content=req.message,
        provider=req.provider,
    )
    store.add_message(conv_id, user_msg)

    history = _build_history(conv)

    agent = _build_agent(req.provider)

    result_text = ""
    tool_calls_made = []

    def on_tool_call(name, args):
        tool_calls_made.append(ToolCallInfo(name=name, arguments=args))

    try:
        result_text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: agent.run(req.message, history, on_tool_call=on_tool_call)
        )
    except Exception as e:
        logger.error("agent_error=%s", e)
        result_text = f"I encountered an error: {str(e)[:200]}"

    assistant_msg = ChatMessage(
        role=MessageRole.ASSISTANT,
        content=result_text,
        tool_calls=tool_calls_made,
        provider=req.provider or conv.provider,
    )
    store.add_message(conv_id, assistant_msg)

    return ChatResponse(message=assistant_msg)


# ── Chat (SSE streaming) ──────────────────────────────────────

@app.post("/api/conversations/{conv_id}/chat/stream")
async def chat_stream(conv_id: str, req: ChatRequest):
    """SSE streaming chat endpoint."""
    store = get_store()
    conv = store.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Store user message (backend owns persistence)
    user_msg = ChatMessage(
        role=MessageRole.USER,
        content=req.message,
        provider=req.provider,
    )
    store.add_message(conv_id, user_msg)

    # Build history for LLM (includes system prompt + prior messages)
    history = _build_history(conv)

    async def generate():
        full_text = ""
        tool_calls_made = []
        tool_results_seen = []

        try:
            agent = _build_agent(req.provider)

            # Collect tool calls/results during execution
            collected_tool_calls = []
            collected_tool_results = []

            def on_tool_call(name, args):
                collected_tool_calls.append({"name": name, "arguments": args})

            # Run agent in executor (blocking LLM + tool calls)
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent.run(req.message, history, on_tool_call=on_tool_call)
            )
            full_text = result_text

            # Send all tool calls that were made
            for tc in collected_tool_calls:
                tc_info = ToolCallInfo(name=tc["name"], arguments=tc["arguments"])
                tool_calls_made.append(tc_info)
                yield f"data: {json.dumps({'type': 'tool_call', 'name': tc['name'], 'arguments': tc['arguments']})}\n\n"

            # Send the text response
            if full_text:
                yield f"data: {json.dumps({'type': 'text', 'content': full_text})}\n\n"

        except Exception as e:
            logger.error("stream_agent_error=%s", e)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)[:200]})}\n\n"

        # Store assistant response
        assistant_msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=full_text,
            tool_calls=tool_calls_made,
            provider=req.provider or conv.provider,
        )
        store.add_message(conv_id, assistant_msg)

        yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_msg.id})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _build_history(conv: Conversation) -> list:
    """Build LLM history from conversation messages."""
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in conv.messages:
        role = m.role.value
        if role == "tool":
            for tr in m.tool_results:
                history.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id or "",
                    "name": tr.name or "",
                    "content": json.dumps(tr.result) if isinstance(tr.result, dict) else str(tr.result),
                })
        elif role == "assistant" and m.tool_calls:
            msg = {"role": "assistant", "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {"id": tc.id or "", "type": "function",
                     "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in m.tool_calls
                ]
            history.append(msg)
        else:
            history.append({"role": role, "content": m.content})
    return history


# ── Tasks ──────────────────────────────────────────────────────

@app.get("/api/tasks", response_model=TaskListResponse)
def list_tasks(conversation_id: Optional[str] = None):
    tm = get_task_manager()
    if conversation_id:
        tasks = tm.get_tasks_for_conversation(conversation_id)
    else:
        tasks = tm.get_active_tasks()
    return TaskListResponse(tasks=tasks)


@app.get("/api/tasks/{task_id}", response_model=TaskInfo)
def get_task(task_id: str):
    task = get_task_manager().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    tm = get_task_manager()
    task = tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.cancellable:
        raise HTTPException(status_code=400, detail="Task is not cancellable")
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Task is already {task.status.value}")

    if tm.request_cancel(task_id):
        return {"success": True, "status": "cancel_requested"}
    return {"success": False, "error": "Could not request cancellation"}


# ── Voice ──────────────────────────────────────────────────────

@app.get("/api/voice/status")
def voice_status():
    stt = get_stt()
    tts = get_tts()
    return {
        "stt_available": stt.available,
        "tts_available": tts.available,
    }


@app.post("/api/voice/transcribe")
async def transcribe_audio(request: Request):
    """Accept audio bytes and return transcribed text."""
    stt = get_stt()
    if not stt.available:
        return VoiceTranscriptionResponse(text="", confidence=0.0)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="No audio data")

    result = stt.transcribe(body)
    return VoiceTranscriptionResponse(
        text=result.get("text", ""),
        confidence=result.get("confidence", 0.0),
    )


@app.post("/api/voice/synthesize")
async def synthesize_speech(req: TTSRequest):
    """Convert text to speech."""
    tts = get_tts()
    if not tts.available:
        return TTSResponse(available=False, error="TTS not available")

    result = tts.synthesize(req.text, req.voice)
    if "error" in result:
        return TTSResponse(available=False, error=result["error"])

    audio_bytes = result.get("audio_bytes", b"")
    mime = result.get("mime_type", "audio/wav")
    return StreamingResponse(iter([audio_bytes]), media_type=mime)

# ── Control Plane / Workers ────────────────────────────────────
# Development foundation: real authentication can supply organization scope
# here later; these endpoints still enforce scope inside the domain service.

@app.post("/api/control/organizations")
def create_organization(body: dict):
    try:
        return get_control_plane().public(get_control_plane().create_organization(body.get("name", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/control/projects")
def create_project(body: dict):
    try:
        item = get_control_plane().create_project(body.get("organization_id", ""), body.get("name", ""), body.get("description", ""))
        return get_control_plane().public(item)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/control/infrastructure")
def create_infrastructure(body: dict):
    try:
        item = get_control_plane().create_infrastructure(body.get("organization_id", ""), body.get("project_id", ""), body.get("name", ""), body.get("type", "local"))
        return get_control_plane().public(item)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.get("/api/control/infrastructure")
def list_control_infrastructure(organization_id: str):
    cp = get_control_plane()
    return {"infrastructure": [cp.public(item) for item in cp.list_infrastructure(organization_id)]}


@app.post("/api/control/enrollments")
def create_worker_enrollment(body: dict):
    try:
        token = get_control_plane().create_enrollment(body.get("organization_id", ""), body.get("project_id", ""), body.get("infrastructure_id", ""), body.get("ttl_minutes", 15))
        # The token is intentionally returned exactly once, only at creation.
        return {"enrollment_token": token, "expires_in_minutes": min(max(1, body.get("ttl_minutes", 15)), 60)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.post("/api/workers/register")
def register_worker(body: dict):
    try:
        worker, worker_token = get_control_plane().register_worker(body.get("enrollment_token", ""), name=body.get("name", "worker"), version=body.get("version", "unknown"), platform=body.get("platform", "unknown"), hostname=body.get("hostname", "unknown"), capabilities=body.get("capabilities", []))
        return {"worker": get_control_plane().public(worker), "worker_token": worker_token}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.post("/api/workers/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str, body: dict, request: Request):
    token = request.headers.get("X-Worker-Token", "")
    try:
        worker = get_control_plane().heartbeat(worker_id, token, version=body.get("version", "unknown"), platform=body.get("platform", "unknown"), hostname=body.get("hostname", "unknown"), capabilities=body.get("capabilities", []), status=body.get("status", "online"))
        return {"worker": get_control_plane().public(worker)}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

# Terminal

@app.get("/terminal", response_class=HTMLResponse)
def terminal_page():
    html_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'terminal.html')
    with open(html_path) as f:
        return HTMLResponse(content=f.read())
