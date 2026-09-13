"""
Pydantic models for the JARVIS web API.

Request/response schemas for chat, providers, conversations, tasks, and voice.
"""
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


# ── Messages ───────────────────────────────────────────────────

class ToolCallInfo(BaseModel):
    id: Optional[str] = None
    name: str
    arguments: Dict[str, Any] = {}


class ToolResultInfo(BaseModel):
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    result: Dict[str, Any] = {}


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: MessageRole
    content: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tool_calls: List[ToolCallInfo] = []
    tool_results: List[ToolResultInfo] = []
    provider: Optional[str] = None
    # For task cards embedded in messages
    task_id: Optional[str] = None


# ── Conversations ──────────────────────────────────────────────

class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "New Chat"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    provider: str = "groq"
    messages: List[ChatMessage] = []


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


# ── Chat ───────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    provider: Optional[str] = None  # request-scoped provider override
    input_mode: str = "text"



class ChatResponse(BaseModel):
    message: ChatMessage
    task_id: Optional[str] = None  # if a long-running task was spawned


# ── Providers ──────────────────────────────────────────────────

class ProviderInfo(BaseModel):
    id: str
    name: str
    model: str
    available: bool
    status: str = "available"  # available, rate_limited, unavailable, cooldown


class ProviderListResponse(BaseModel):
    providers: List[ProviderInfo]
    active_provider: str


class ProviderSwitchRequest(BaseModel):
    provider: str


# ── Tasks ──────────────────────────────────────────────────────

class TaskInfo(BaseModel):
    id: str
    conversation_id: str
    title: str
    status: TaskStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    progress: float = 0.0
    current_step: str = ""
    tool_name: Optional[str] = None
    cancellable: bool = True
    result: Optional[Dict[str, Any]] = None


class TaskCancelRequest(BaseModel):
    pass  # no body needed — the task_id is in the URL


class TaskListResponse(BaseModel):
    tasks: List[TaskInfo]


# ── Voice ──────────────────────────────────────────────────────

class VoiceTranscriptionResponse(BaseModel):
    text: str
    confidence: float = 1.0


class TTSRequest(BaseModel):
    text: str
    voice: str = "default"


class TTSResponse(BaseModel):
    audio_url: Optional[str] = None
    available: bool = False
    error: Optional[str] = None


# ── Health ─────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "phase7"
    provider: str = ""
