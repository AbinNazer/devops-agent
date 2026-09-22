"""Severity classification + tone selection.

Severity is derived from what the conversation is actually about — the
user's words plus any known incident state — never from speculation.
Tone adjustments are also deterministic and evidence-based.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Words that signal urgency/degradation in the user's message or in
# detected incident context. Kept deliberately small and unambiguous —
# false CRITICAL classification suppresses personality, so we prefer
# under-triggering.
_CRITICAL_MARKERS = (
    "production is down", "outage", "site is down", "everything is down",
    "critical", "emergency", "data loss", "security breach", "breach",
    "asap", "urgent",
)
_ELEVATED_MARKERS = (
    "crashing", "crash loop", "failing", "error", "errors", "degraded",
    "unhealthy", "not responding", "restart", "down", "broken", "slow",
    "oom", "memory leak", "disk full",
)


class Severity(Enum):
    LOW = "low"              # casual chat, simple questions, routine checks
    ELEVATED = "elevated"    # something degraded but under control
    CRITICAL = "critical"    # production outage, security, destructive context


@dataclass(frozen=True)
class ToneDirective:
    severity: Severity
    verbosity: str          # "minimal" | "concise" | "standard" | "detailed"
    style: str              # "casual" | "technical" | "supportive" | "serious"
    humor_allowed: bool
    reasoning: str          # why this tone was chosen — goes into the prompt


def classify_severity(text: str, incident_severity: str = "") -> Severity:
    """Classify conversation severity from user text + known incident state."""
    combined = f"{incident_severity} {text}".lower()
    if incident_severity.lower() in {"critical", "high"}:
        return Severity.CRITICAL
    if any(m in combined for m in _CRITICAL_MARKERS):
        return Severity.CRITICAL
    if any(m in combined for m in _ELEVATED_MARKERS):
        return Severity.ELEVATED
    return Severity.LOW


def select_tone(severity: Severity, user_style: str, base_verbosity: float,
                base_formality: float) -> ToneDirective:
    """Deterministic tone selection. Severity overrides user style."""
    if severity is Severity.CRITICAL:
        return ToneDirective(
            severity=severity, verbosity="detailed", style="serious",
            humor_allowed=False,
            reasoning="critical severity — clarity and correctness outrank personality",
        )
    if user_style == "frustrated":
        return ToneDirective(
            severity=severity, verbosity="concise", style="supportive",
            humor_allowed=False,
            reasoning="user is frustrated — calm and supportive, no jokes",
        )
    if user_style == "in_a_hurry":
        return ToneDirective(
            severity=severity, verbosity="minimal", style="casual",
            humor_allowed=True,
            reasoning="user wants speed — answer in as few words as possible",
        )
    if user_style == "joking":
        return ToneDirective(
            severity=severity, verbosity="concise", style="casual",
            humor_allowed=True,
            reasoning="user is joking — playful tone is welcome",
        )
    if user_style == "technical":
        return ToneDirective(
            severity=severity,
            verbosity="standard" if base_verbosity >= 0.4 else "concise",
            style="technical", humor_allowed=True,
            reasoning="technical conversation — precise and direct",
        )
    if user_style == "serious":
        return ToneDirective(
            severity=severity, verbosity="standard", style="serious",
            humor_allowed=False,
            reasoning="user is serious — keep personality restrained",
        )
    if user_style == "asking_for_explanation":
        return ToneDirective(
            severity=severity, verbosity="detailed", style="technical",
            humor_allowed=True,
            reasoning="user asked for an explanation — go deeper",
        )
    # Default / casual
    verbosity = "concise" if base_verbosity < 0.5 else "standard"
    return ToneDirective(
        severity=severity, verbosity=verbosity, style="casual",
        humor_allowed=True,
        reasoning="default conversation — natural, concise, personality welcome",
    )
