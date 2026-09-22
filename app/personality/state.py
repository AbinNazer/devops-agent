"""Conversation state: lightweight tracking of what the conversation is
currently about, so follow-ups like "restart it", "why?", or "did that
fix it?" resolve to the right thing without the user repeating context.

The LLM still does the actual language understanding — this module gives
it a structured summary of the conversation's active subjects. State is
in-memory per process (matches the session architecture) and is not
persisted to memory.db.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Component-ish words we can auto-detect as the active subject. Deliberately
# matches the vocabulary already used across tools/intelligence.
_COMPONENT_HINTS = (
    "jenkins", "nginx", "redis", "postgres", "postgresql", "mysql",
    "erp", "k3s", "kafka", "prometheus", "grafana", "docker", "vps", "aws",
)

# Arbitrary named subjects: "check the billing container", "restart the
# payments service" — project/service names that aren't in the known list.
_SUBJECT_PATTERN = re.compile(
    r"\b(?:the\s+)?([a-z][\w.\-]{1,30}?)\s+"
    r"(?:container|service|pod|deployment|project|stack|app)\b",
    re.IGNORECASE,
)


@dataclass
class ConversationState:
    """What the current conversation is about. Mutated by the agent loop."""
    current_task: Optional[str] = None            # e.g. "container_diagnosis"
    current_project: Optional[str] = None
    current_environment: Optional[str] = None     # "local" | "vps" | "aws"
    current_server: Optional[str] = None
    current_container: Optional[str] = None
    current_service: Optional[str] = None
    current_incident_id: Optional[str] = None
    recent_user_intent: Optional[str] = None
    pending_question: Optional[str] = None        # question JARVIS asked, awaiting answer
    recent_findings: List[str] = field(default_factory=list)
    recent_actions: List[str] = field(default_factory=list)
    last_subject: Optional[str] = None            # best "it" candidate
    updated_at: float = field(default_factory=time.time)

    def observe(self, user_input: str) -> None:
        """Update state from a user message. Cheap heuristics only — the
        LLM provides the real understanding via its conversation history."""
        text = (user_input or "").strip()
        if not text:
            return
        self.updated_at = time.time()
        lower = text.lower()
        matched = False
        for hint in _COMPONENT_HINTS:
            if hint in lower:
                self.last_subject = hint
                if hint in {"docker", "k3s", "vps", "aws"}:
                    self.current_environment = "vps" if hint == "vps" else None
                else:
                    self.current_container = self.current_container or hint
                matched = True
                break
        if not matched:
            m = _SUBJECT_PATTERN.search(lower)
            if m:
                self.last_subject = m.group(1)
        self.recent_user_intent = text[:200]

    def observe_tool_result(self, tool_name: str, summary: str) -> None:
        """Record what tools have been looking at, so "it" has a referent."""
        if tool_name in {"run_diagnostic", "restart_container", "restart_service"}:
            self.recent_actions.append(f"{tool_name}: {summary}"[:200])
            self.recent_actions = self.recent_actions[-5:]
        elif summary:
            self.recent_findings.append(f"{tool_name}: {summary}"[:200])
            self.recent_findings = self.recent_findings[-5:]
        self.updated_at = time.time()

    def set_pending_question(self, question: str) -> None:
        self.pending_question = question[:200]
        self.updated_at = time.time()

    def summary(self) -> Dict[str, Optional[str]]:
        """Compact snapshot for the system prompt. Only includes what's set."""
        snap: Dict[str, Optional[str]] = {}
        for key in ("current_task", "current_project", "current_environment",
                    "current_server", "current_container", "current_service",
                    "current_incident_id", "last_subject", "pending_question"):
            value = getattr(self, key)
            if value:
                snap[key] = value
        if self.recent_findings:
            snap["recent_findings"] = " | ".join(self.recent_findings[-3:])
        if self.recent_actions:
            snap["recent_actions"] = " | ".join(self.recent_actions[-3:])
        return snap


# One state per conversation key (session id / conversation id). Default key
# covers the CLI's single conversation.
_STATES: Dict[str, ConversationState] = {}
_LOCK = threading.Lock()


def get_conversation_state(key: str = "default") -> ConversationState:
    with _LOCK:
        if key not in _STATES:
            _STATES[key] = ConversationState()
        return _STATES[key]


def reset_conversation_state(key: str = "default") -> None:
    with _LOCK:
        _STATES.pop(key, None)
