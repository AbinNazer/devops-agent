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
import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from app.config import Config
from app.agent import Agent, SYSTEM_PROMPT
from app.llm_router import build_router, LLMRouter, ProviderState
from app.personality import get_conversation_state
from app.personality.preferences_bridge import load_communication_preferences as _load_communication_preferences
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
from app.auth import authenticate, create_session, delete_session, delete_all_sessions, valid_session, session_user, hash_password, verify_password
from app.tenancy import registry, new_user_id
from app.repositories import saas_repository
from app.provider_registry import catalog as provider_catalog
from app.conversation_repository import PostgresConversationRepository, ConversationOwnershipError
from app.research import web_search, learn_tool, SKILLS_DIR

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
    allow_origins=Config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_security_attempts: dict[str, list[float]] = {}


def _rate_limited(key: str, limit: int, window: int) -> bool:
    now = time.monotonic()
    attempts = [stamp for stamp in _security_attempts.get(key, []) if now - stamp < window]
    if len(attempts) >= limit:
        _security_attempts[key] = attempts
        return True
    attempts.append(now)
    _security_attempts[key] = attempts
    return False


@app.middleware("http")
async def security_headers(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in Config.ALLOWED_ORIGINS and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return Response(content='{"detail":"Origin not allowed"}', status_code=403, media_type="application/json")
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), payment=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'")
    if Config.COOKIE_SECURE:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

# Serve static files (terminal.js)
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static'))
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def web_app():
    """Serve the lightweight browser UI without changing agent behavior."""
    html_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'index.html')
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/sw.js")
def service_worker():
    """Serve the PWA service worker with a root scope so home-screen launches can cache the app shell."""
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    username: str
    password: str
    confirm_password: str


class SettingsUpdate(BaseModel):
    values: dict = {}


class ProviderCredentialUpdate(BaseModel):
    api_key: str


class ResearchRequest(BaseModel):
    query: str = ""
    tool: str = ""
    version: str = ""
    refresh: bool = False


class InfrastructureTarget(BaseModel):
    name: str
    kind: str
    host: str = ""
    port: int | None = None
    username: str = ""
    region: str = ""
    endpoint: str = ""
    notes: str = ""


class InfrastructureTargetUpdate(InfrastructureTarget):
    id: str | None = None


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(f"login:{client_ip}", Config.LOGIN_RATE_LIMIT, Config.LOGIN_RATE_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many authentication attempts. Try again later.")
    valid = authenticate(body.username, body.password)
    if Config.DATABASE_ENABLED and Config.DATABASE_URL:
        from app.postgres import connect
        with connect(Config.DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT password_hash, status FROM users WHERE username = %s", (body.username,))
                row = cursor.fetchone()
                if row:
                    valid = row[1] == "active" and bool(row[0]) and verify_password(body.password, row[0])
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response.set_cookie("jarvis_session", create_session(body.username), httponly=True, samesite="lax", secure=Config.COOKIE_SECURE, max_age=86400, path="/")
    return {"authenticated": True}


@app.post("/api/auth/register")
def register(body: RegisterRequest, response: Response, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(f"register:{client_ip}", 5, Config.LOGIN_RATE_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many account attempts. Try again later.")
    if len(body.first_name.strip()) < 1 or len(body.last_name.strip()) < 1:
        raise HTTPException(status_code=400, detail="First and last name are required")
    if "@" not in body.email or len(body.email.strip()) < 5:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if len(body.username.strip()) < 3 or len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Username must have 3 characters and password 8 characters")
    if body.password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    if not Config.DATABASE_ENABLED or not Config.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Registration requires database-backed accounts")
    from app.postgres import connect
    user_id = new_user_id(body.username.strip())
    with connect(Config.DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE username = %s OR lower(email) = lower(%s)", (body.username.strip(), body.email.strip()))
            if cursor.fetchone(): raise HTTPException(status_code=409, detail="Username or email already exists")
            cursor.execute("INSERT INTO users (id, username, first_name, last_name, email, password_hash) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, body.username.strip(), body.first_name.strip(), body.last_name.strip(), body.email.strip(), hash_password(body.password)))
        connection.commit()
    response.set_cookie("jarvis_session", create_session(body.username.strip()), httponly=True, samesite="lax", secure=Config.COOKIE_SECURE, max_age=86400, path="/")
    return {"authenticated": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    token = request.cookies.get("jarvis_session")
    username = session_user(token)
    if not username:
        return {"authenticated": False}
    context = registry.context_for(new_user_id(username))
    return {"authenticated": True, "user_id": context.user_id, "organization": {"id": context.organization.id, "name": context.organization.name}, "role": context.role.value, "permissions": sorted(context.can("*") and {"*"} or {p for p in ("infrastructure.read", "terminal.access", "incident.manage", "settings.manage", "team.manage") if context.can(p)})}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    delete_session(request.cookies.get("jarvis_session"))
    response.delete_cookie("jarvis_session", path="/")
    return {"authenticated": False}


@app.post("/api/auth/logout-all")
def logout_all(request: Request, response: Response):
    username = session_user(request.cookies.get("jarvis_session"))
    if username:
        delete_all_sessions(username)
    response.delete_cookie("jarvis_session", path="/")
    return {"authenticated": False}


@app.post("/api/auth/password")
def change_password(body: PasswordChangeRequest, request: Request):
    username = session_user(request.cookies.get("jarvis_session"))
    if not username or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Unable to change password")
    if not Config.DATABASE_ENABLED or not Config.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Password changes require database-backed accounts")
    from app.postgres import connect
    from app.tenancy import new_user_id
    with connect(Config.DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
            row = cursor.fetchone()
            if not row or not row[0] or not verify_password(body.current_password, row[0]):
                raise HTTPException(status_code=400, detail="Unable to change password")
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(body.new_password), new_user_id(username)))
        connection.commit()
    delete_all_sessions(username)
    return {"success": True, "message": "Password changed. Please sign in again."}


@app.post("/api/research/search")
def research_search(body: ResearchRequest, request: Request):
    if not session_user(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    return web_search(body.query)


@app.post("/api/research/learn")
def research_learn(body: ResearchRequest, request: Request):
    if not session_user(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not body.tool.strip():
        raise HTTPException(status_code=400, detail="tool is required")
    return learn_tool(body.tool, body.refresh, body.version)


@app.post("/api/research/learn/stream")
async def research_learn_stream(body: ResearchRequest, request: Request):
    if not session_user(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not body.tool.strip():
        raise HTTPException(status_code=400, detail="tool is required")

    async def events():
        yield "event: research\ndata: " + json.dumps({"stage": "started", "tool": body.tool}) + "\n\n"
        yield "event: research\ndata: " + json.dumps({"stage": "searching_official_docs"}) + "\n\n"
        result = await asyncio.to_thread(learn_tool, body.tool, body.refresh, body.version)
        yield "event: research\ndata: " + json.dumps({"stage": "completed" if result.get("success") else "failed", "result": result}) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/research/skills")
def research_skills(request: Request):
    if not session_user(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"skills": [{"name": path.parent.name, "path": str(path.relative_to(SKILLS_DIR.parent))} for path in SKILLS_DIR.glob("*/SKILL.md")]} if SKILLS_DIR.exists() else {"skills": []}


@app.post("/api/research/skills/{skill}/refresh")
def refresh_research_skill(skill: str, request: Request):
    if not session_user(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not re.fullmatch(r"[a-z0-9-]{1,60}", skill):
        raise HTTPException(status_code=400, detail="Invalid skill name")
    return learn_tool(skill.replace("-", " "), refresh=True)


def _settings_identity(request: Request):
    username = session_user(request.cookies.get("jarvis_session"))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = new_user_id(username)
    context = registry.context_for(user_id)
    if user_id not in saas_repository.users:
        saas_repository.add_user(username, user_id=user_id)
    if context.organization.id not in saas_repository.organizations:
        saas_repository.add_organization(context.organization.name, user_id, context.organization.id)
    if Config.DATABASE_ENABLED and Config.DATABASE_URL:
        from app.postgres import connect
        with connect(Config.DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO users (id, username) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING", (user_id, username))
                cursor.execute("INSERT INTO organizations (id, name, owner_user_id) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING", (context.organization.id, context.organization.name, user_id))
                cursor.execute("INSERT INTO organization_members (organization_id, user_id, role) VALUES (%s, %s, 'owner') ON CONFLICT (organization_id, user_id) DO NOTHING", (context.organization.id, user_id))
                cursor.execute("INSERT INTO user_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
                cursor.execute("INSERT INTO organization_settings (organization_id) VALUES (%s) ON CONFLICT (organization_id) DO NOTHING", (context.organization.id,))
            connection.commit()
    return user_id, context.organization.id


@app.get("/api/settings/user")
def get_user_settings(request: Request):
    user_id, _ = _settings_identity(request)
    record = saas_repository.get_user_settings(user_id)
    return {"scope": "user", "values": record.values, "updated_at": record.updated_at}


@app.put("/api/settings/user")
def update_user_settings(body: SettingsUpdate, request: Request):
    user_id, _ = _settings_identity(request)
    record = saas_repository.update_settings(saas_repository.get_user_settings(user_id), body.values)
    return {"scope": "user", "values": record.values, "updated_at": record.updated_at}


@app.get("/api/settings/organization")
def get_organization_settings(request: Request):
    _, organization_id = _settings_identity(request)
    record = saas_repository.get_organization_settings(organization_id)
    return {"scope": "organization", "values": record.values, "updated_at": record.updated_at}


@app.put("/api/settings/organization")
def update_organization_settings(body: SettingsUpdate, request: Request):
    _, organization_id = _settings_identity(request)
    record = saas_repository.update_settings(saas_repository.get_organization_settings(organization_id), body.values)
    return {"scope": "organization", "values": record.values, "updated_at": record.updated_at}


@app.get("/api/settings/infrastructure")
def list_infrastructure_targets(request: Request):
    """List organization-owned VPS/AWS connection metadata without secrets."""
    _, organization_id = _settings_identity(request)
    record = saas_repository.get_organization_settings(organization_id)
    targets = record.values.get("infrastructure_targets", [])
    return {"targets": targets}


@app.post("/api/settings/infrastructure")
def add_infrastructure_target(body: InfrastructureTargetUpdate, request: Request):
    """Register a VPS, AWS account, or other infrastructure target."""
    _, organization_id = _settings_identity(request)
    if not body.name.strip() or body.kind not in {"vps", "aws"}:
        raise HTTPException(status_code=400, detail="Name and a supported target type (vps or aws) are required")
    record = saas_repository.get_organization_settings(organization_id)
    targets = list(record.values.get("infrastructure_targets", []))
    item = body.model_dump(exclude_none=True)
    item["id"] = body.id or f"target_{uuid.uuid4().hex[:12]}"
    item["name"] = item["name"].strip()
    targets = [existing for existing in targets if existing.get("id") != item["id"]]
    targets.append(item)
    saas_repository.update_settings(record, {"infrastructure_targets": targets})
    return {"target": item, "targets": targets}


@app.delete("/api/settings/infrastructure/{target_id}")
def delete_infrastructure_target(target_id: str, request: Request):
    _, organization_id = _settings_identity(request)
    record = saas_repository.get_organization_settings(organization_id)
    targets = [item for item in record.values.get("infrastructure_targets", []) if item.get("id") != target_id]
    saas_repository.update_settings(record, {"infrastructure_targets": targets})
    return {"targets": targets}


@app.get("/api/settings/providers")
def provider_settings(request: Request):
    user_id, _ = _settings_identity(request)
    configured = {name for name, key in (("groq", Config.GROQ_API_KEY), ("openai", Config.OPENAI_API_KEY), ("gemini", Config.GEMINI_API_KEY), ("anthropic", Config.ANTHROPIC_API_KEY)) if key}
    configured.add("ollama") if Config.OLLAMA_HOST else None
    preferences = saas_repository.get_user_settings(user_id).values.get("provider_preferences", {})
    return {"providers": [{**item, "configured": item["id"] in configured, "enabled": preferences.get("enabled", {}).get(item["id"], True)} for item in provider_catalog()], "preferences": preferences}


@app.put("/api/settings/providers")
def update_provider_settings(body: SettingsUpdate, request: Request):
    user_id, _ = _settings_identity(request)
    allowed = {"primary_provider", "primary_model", "fallback_enabled", "enabled"}
    values = {key: value for key, value in body.values.items() if key in allowed}
    record = saas_repository.get_user_settings(user_id)
    current = record.values.get("provider_preferences", {})
    current.update(values)
    saas_repository.update_settings(record, {"provider_preferences": current})
    return {"preferences": current}


@app.put("/api/settings/providers/{provider_id}/credential")
def save_provider_credential(provider_id: str, body: ProviderCredentialUpdate, request: Request):
    user_id, _ = _settings_identity(request)
    valid_ids = {item["id"] for item in provider_catalog()}
    if provider_id not in valid_ids:
        raise HTTPException(status_code=400, detail="Unknown provider")
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    if not Config.DATABASE_ENABLED or not Config.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Persistent secret storage is not enabled")
    from app.secret_store import EncryptedSecretStore
    EncryptedSecretStore(Config.DATABASE_URL).put(user_id, f"provider:{provider_id}:api_key", body.api_key)
    return {"provider": provider_id, "configured": True, "masked_key": "••••••••"}

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


def _pg_repository(request: Request) -> PostgresConversationRepository | None:
    if not Config.DATABASE_ENABLED or not Config.DATABASE_URL:
        return None
    _user_id, organization_id = _settings_identity(request)
    return PostgresConversationRepository(Config.DATABASE_URL)


def _pg_owner(request: Request) -> tuple[str, str]:
    user_id, organization_id = _settings_identity(request)
    return user_id, organization_id


def _pg_model(data: dict) -> Conversation:
    messages = []
    for item in data.get("messages", []):
        messages.append(ChatMessage(id=item["id"], role=MessageRole(item["role"]), content=item.get("content", ""), provider=item.get("provider"), timestamp=item.get("created_at") or datetime.utcnow()))
    return Conversation(id=data["id"], title=data["title"], provider=data["provider"], created_at=data.get("created_at") or datetime.utcnow(), updated_at=data.get("updated_at") or datetime.utcnow(), messages=messages)


def _build_agent(provider_override: Optional[str] = None, conversation_id: Optional[str] = None) -> Agent:
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
        conversation_state=get_conversation_state(conversation_id or "api-default"),
        preference_loader=_load_communication_preferences,
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

@app.get("/api/usage")
def llm_usage(request: Request, period: str = "session"):
    """Provider-neutral LLM token usage summary."""
    if not session_user(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    from app.usage_tracking import get_usage_tracker
    import time
    from app.usage_tracking import SESSION_STARTED
    since = 0 if period == "all" else time.time() - 86400 if period == "today" else SESSION_STARTED
    return get_usage_tracker().summary(since)


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
def list_conversations(request: Request):
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        rows = repo.list(organization_id, user_id)
        return [ConversationSummary(id=r["id"], title=r["title"], created_at=r["created_at"], updated_at=r["updated_at"], message_count=0) for r in rows]
    return get_store().list_all()


@app.post("/api/conversations", response_model=Conversation)
def create_conversation(request: Request, title: str = "New Chat"):
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        return _pg_model({**repo.create(organization_id, user_id, title=title), "messages": []})
    return get_store().create(title=title)


@app.get("/api/conversations/search")
def search_conversations(q: str = ""):
    if not q:
        return get_store().list_all()
    return get_store().search(q)


@app.get("/api/conversations/{conv_id}", response_model=Conversation)
def get_conversation(conv_id: str, request: Request):
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        data = repo.get_with_messages(conv_id, organization_id, user_id)
        if not data: raise HTTPException(status_code=404, detail="Conversation not found")
        return _pg_model(data)
    conv = get_store().get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.put("/api/conversations/{conv_id}/rename")
def rename_conversation(conv_id: str, body: dict, request: Request):
    title = body.get("title", "")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        try: repo.rename(conv_id, organization_id, user_id, title)
        except ConversationOwnershipError: raise HTTPException(status_code=404, detail="Conversation not found")
        return {"success": True}
    if not get_store().rename(conv_id, title):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str, request: Request):
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        try: repo.delete(conv_id, organization_id, user_id)
        except ConversationOwnershipError: raise HTTPException(status_code=404, detail="Conversation not found")
        return {"success": True}
    if not get_store().delete(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}


@app.post("/api/conversations/{conv_id}/provider")
def set_conversation_provider(conv_id: str, body: ProviderSwitchRequest, request: Request):
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        try: repo.update_provider(conv_id, organization_id, user_id, body.provider)
        except ConversationOwnershipError: raise HTTPException(status_code=404, detail="Conversation not found")
        switch_provider(body)
        return {"success": True, "provider": body.provider}
    if not get_store().update_provider(conv_id, body.provider):
        raise HTTPException(status_code=404, detail="Conversation not found")
    switch_provider(body)
    return {"success": True, "provider": body.provider}


# ── Chat (non-streaming) ──────────────────────────────────────

@app.post("/api/conversations/{conv_id}/chat")
async def chat(conv_id: str, req: ChatRequest, request: Request):
    """Send a message and get a response (non-streaming)."""
    store = get_store()
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        data = repo.get_with_messages(conv_id, organization_id, user_id)
        conv = _pg_model(data) if data else None
    else:
        conv = store.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_msg = ChatMessage(
        role=MessageRole.USER,
        content=req.message,
        provider=req.provider,
    )
    if repo:
        repo.add_message(conv_id, organization_id, user_id, user_msg.id, user_msg.role.value, user_msg.content, user_msg.provider)
    else:
        store.add_message(conv_id, user_msg)

    history = _build_history(conv)

    agent = _build_agent(req.provider, conversation_id=conv_id)

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
    if repo:
        repo.add_message(conv_id, organization_id, user_id, assistant_msg.id, assistant_msg.role.value, assistant_msg.content, assistant_msg.provider)
    else:
        store.add_message(conv_id, assistant_msg)

    return ChatResponse(message=assistant_msg)


# ── Chat (SSE streaming) ──────────────────────────────────────

@app.post("/api/conversations/{conv_id}/chat/stream")
async def chat_stream(conv_id: str, req: ChatRequest, request: Request):
    """SSE streaming chat endpoint."""
    store = get_store()
    repo = _pg_repository(request)
    if repo:
        user_id, organization_id = _pg_owner(request)
        data = repo.get_with_messages(conv_id, organization_id, user_id)
        conv = _pg_model(data) if data else None
    else:
        conv = store.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Store user message (backend owns persistence)
    user_msg = ChatMessage(
        role=MessageRole.USER,
        content=req.message,
        provider=req.provider,
    )
    if repo:
        repo.add_message(conv_id, organization_id, user_id, user_msg.id, user_msg.role.value, user_msg.content, user_msg.provider)
    else:
        store.add_message(conv_id, user_msg)

    # Build history for LLM (includes system prompt + prior messages)
    history = _build_history(conv)

    async def generate():
        full_text = ""
        tool_calls_made = []

        # Sentinel sent by agent thread when it finishes (or errors out)
        _DONE = object()

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _put(event: dict):
            """Thread-safe push from agent thread into the async queue."""
            loop.call_soon_threadsafe(q.put_nowait, event)

        try:
            agent = _build_agent(req.provider, conversation_id=conv_id)

            # --- Callbacks invoked on the agent thread ---
            def on_tool_call(name, args):
                _put({"type": "tool_call", "name": name, "arguments": args})

            def on_tool_result(name, result):
                _put({"type": "tool_result", "name": name,
                      "success": result.get("success", True)})

            # Immediately acknowledge receipt so the browser stops the spinner
            _put({"type": "status", "content": "Thinking\u2026"})

            def run_agent():
                try:
                    text = agent.run(
                        req.message, history,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                    )
                    _put({"type": "text", "content": text})
                except Exception as exc:
                    _put({"type": "error", "content": str(exc)[:200]})
                finally:
                    _put({"__sentinel__": True})

            # Run agent in default thread pool — does NOT block the event loop
            loop.run_in_executor(None, run_agent)

            # --- Drain queue and stream to client ---
            while True:
                event = await q.get()
                if event.get("__sentinel__"):
                    break

                et = event.get("type")
                if et == "tool_call":
                    tc_info = ToolCallInfo(name=event["name"], arguments=event.get("arguments", {}))
                    tool_calls_made.append(tc_info)
                elif et == "text":
                    full_text = event.get("content", "")

                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error("stream_agent_error=%s", e)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)[:200]})}\n\n"

        # Persist assistant message
        assistant_msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=full_text,
            tool_calls=tool_calls_made,
            provider=req.provider or conv.provider,
        )
        if repo:
            repo.add_message(conv_id, organization_id, user_id, assistant_msg.id, assistant_msg.role.value, assistant_msg.content, assistant_msg.provider)
        else:
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

# ── Tool Factory & project intelligence endpoints ────────────────────


def _tool_factory_identity(request: Request):
    """Resolve the authenticated user + tenant context for tool endpoints."""
    username = session_user(request.cookies.get("jarvis_session"))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = new_user_id(username)
    context = registry.context_for(user_id)
    return user_id, context


def _require_permission(context, permission: str):
    if not context.can(permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")


def _tool_error_response(exc: Exception):
    from app.tool_factory.validation import ToolValidationError
    if isinstance(exc, ToolValidationError):
        return {"success": False, "errors": [issue.to_dict() for issue in exc.issues]}
    return {"success": False, "error": str(exc)}


@app.get("/api/tools")
def list_factory_tools(request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.read")
    from app.tool_factory.service import get_tool_factory_service
    from app.tool_factory import builtin
    builtin.ensure_builtin_tools()
    service = get_tool_factory_service()
    tools = [definition.to_public() for definition in service.list_tools(context.organization.id)]
    return {"success": True, "tools": tools}


@app.post("/api/tools/validate")
def validate_factory_tool(body: dict, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.manage")
    from app.tool_factory.models import ToolDefinition
    from app.tool_factory.validation import validate_tool_definition
    definition = ToolDefinition(
        name=body.get("name", ""), display_name=body.get("display_name", "") or body.get("name", ""),
        description=body.get("description", ""), category=body.get("category", "general"),
        version=body.get("version", "1.0.0"), author=user_id,
        input_schema=body.get("input_schema", {}), output_schema=body.get("output_schema", {}),
        execution_mode=body.get("execution_mode", "both"), command_template=body.get("command_template", ""),
        allowed_paths=body.get("allowed_paths", []), timeout_seconds=body.get("timeout_seconds", 10),
        max_output_bytes=body.get("max_output_bytes", 16384),
        required_permission=body.get("required_permission", "tools.execute"),
        read_only=body.get("read_only", True),
    )
    issues = validate_tool_definition(definition)
    return {"valid": not issues, "errors": [issue.to_dict() for issue in issues]}


@app.post("/api/tools")
def create_factory_tool(body: dict, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.manage")
    from app.tool_factory.service import get_tool_factory_service, ToolValidationError
    service = get_tool_factory_service()
    try:
        definition = service.create_tool(context.organization.id, body, actor=user_id)
        return {"success": True, "tool": definition.to_public()}
    except ToolValidationError as exc:
        return JSONResponse(status_code=400, content={"success": False, "errors": [issue.to_dict() for issue in exc.issues]})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/tools/{name}")
def get_factory_tool(name: str, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.read")
    from app.tool_factory.service import get_tool_factory_service
    service = get_tool_factory_service()
    try:
        definition = service.get_tool(context.organization.id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    active = service.repository.get_active_version(context.organization.id, name)
    return {"success": True, "tool": definition.to_public(), "active_version": active.version if active else None}


@app.get("/api/tools/{name}/versions")
def list_factory_tool_versions(name: str, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.read")
    from app.tool_factory.service import get_tool_factory_service
    from app.tool_factory.repository import dump_public
    service = get_tool_factory_service()
    try:
        versions = service.list_tool_versions(context.organization.id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"success": True, "versions": [dump_public(version) for version in versions]}


@app.post("/api/tools/{name}/activate")
def activate_factory_tool(name: str, body: dict, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.manage")
    from app.tool_factory.service import get_tool_factory_service
    service = get_tool_factory_service()
    try:
        result = service.activate_tool_version(context.organization.id, name, body.get("version", ""), actor=user_id, reason=body.get("reason", ""))
        return {"success": True, **result}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tools/{name}/deactivate")
def deactivate_factory_tool(name: str, body: dict, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.manage")
    from app.tool_factory.service import get_tool_factory_service
    service = get_tool_factory_service()
    try:
        result = service.deactivate_tool_version(context.organization.id, name, actor=user_id, reason=body.get("reason", ""))
        return {"success": True, **result}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tools/{name}/rollback")
def rollback_factory_tool(name: str, body: dict, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.manage")
    from app.tool_factory.service import get_tool_factory_service
    service = get_tool_factory_service()
    try:
        result = service.rollback_tool_version(context.organization.id, name, actor=user_id, reason=body.get("reason", ""))
        return {"success": True, **result}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/tools/{name}/execute")
def execute_factory_tool(name: str, body: dict, request: Request):
    user_id, context = _tool_factory_identity(request)
    from app.tool_factory.service import get_tool_factory_service
    from app.tool_factory.execution import get_tool_execution_service, ToolExecutionError
    service = get_tool_factory_service()
    try:
        definition = service.get_tool(context.organization.id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _require_permission(context, definition.required_permission)
    execution = get_tool_execution_service()
    try:
        result = execution.execute_tool(name, body.get("arguments", {}), user_id=user_id,
                                        organization_id=context.organization.id, role_can=context.can)
        return result
    except ToolExecutionError as exc:
        status_code = {"tool_not_found": 404, "permission_denied": 403}.get(exc.error_code, 400)
        raise HTTPException(status_code=status_code, detail=str(exc))


@app.get("/api/tools/{name}/audit")
def factory_tool_audit(name: str, request: Request):
    user_id, context = _tool_factory_identity(request)
    _require_permission(context, "tools.manage")
    from app.tool_factory.service import get_tool_factory_service
    service = get_tool_factory_service()
    try:
        records = service.list_audit(context.organization.id, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"success": True, "audits": [record.to_public() for record in records]}


@app.get("/api/project/context")
def project_context(project_path: str = ".", request: Request = None):
    username = session_user(request.cookies.get("jarvis_session"))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    from app.project_intelligence import analyze_project
    try:
        result = analyze_project(project_path, mode="overview")
        return {"success": True, "context": result}
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/project/scan")
def project_scan(body: dict, request: Request):
    username = session_user(request.cookies.get("jarvis_session"))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    from app.project_intelligence import analyze_project, correlate
    path = body.get("project_path", ".")
    try:
        result = analyze_project(path, mode=body.get("mode", "overview"))
        if body.get("error_message") or body.get("container_name"):
            result["correlation"] = correlate(error_message=body.get("error_message", ""),
                                              container_name=body.get("container_name", ""),
                                              project_path=path)
        return result
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/research/progress")
def research_progress(request: Request):
    username = session_user(request.cookies.get("jarvis_session"))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    from app.research import list_research_progress
    return list_research_progress()


@app.get("/api/research/disagreements")
def research_disagreements(request: Request):
    username = session_user(request.cookies.get("jarvis_session"))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    from app.research import source_disagreement_report
    return source_disagreement_report()


# Terminal

@app.get("/terminal", response_class=HTMLResponse)
def terminal_page(request: Request):
    if not valid_session(request.cookies.get("jarvis_session")):
        return RedirectResponse(url="/", status_code=303)
    html_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'terminal.html')
    with open(html_path) as f:
        return HTMLResponse(content=f.read())
