"""Assembles the personality directive injected into the system prompt.

This is the ONLY coupling point between the personality subsystem and the
agent loop: a text block describing how to communicate. It contains no
tool logic, no permissions, and no technical facts — if the personality
module disappeared, the agent would still diagnose infrastructure
identically, just with its default voice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.personality.humor import (
    HumorDecision, HumorLevel, humor_forbidden_response_type, should_use_humor,
)
from app.personality.profile import (
    VOICE_REGISTERS, VOICE_VOLUME_DIAL, PersonalityConfig, VoiceFlavorSettings,
    VoiceRegister, get_profile,
)
from app.personality.response_style import style_guidance
from app.personality.state import ConversationState
from app.personality.tone import Severity, ToneDirective, classify_severity, select_tone

# User conversation-style signals for tone adaptation (Part 7).
_FRUSTRATED_MARKERS = ("wtf", "wth", "wth?", "stupid", "useless", "still not working",
                       "not working again", "broken again", "ugh", "seriously??", "wtf?")
_HURRIED_MARKERS = ("quick", "quickly", "fast", "asap", "hurry", "just tell me",
                    "short answer", "tldr", "tl;dr")
_JOking_MARKERS = ("lol", "haha", "😂", "🤣", "joking", "kidding")
_EXPLAIN_MARKERS = ("explain", "why", "how does", "walk me through", "help me understand",
                    "what does ... mean", "teach me")


@dataclass
class PersonalityContext:
    """The resolved personality decisions for ONE response."""
    profile_name: str = "DEFAULT_JARVIS"
    severity: Severity = Severity.LOW
    tone: Optional[ToneDirective] = None
    humor: Optional[HumorDecision] = None
    user_style: str = "casual"
    # Which volume of the voice this response uses (casual / focused / urgent).
    register: str = "casual"
    preferences: Dict[str, str] = field(default_factory=dict)
    forbidden_openers: List[str] = field(default_factory=list)
    state_summary: Dict[str, str] = field(default_factory=dict)

    @property
    def humor_level(self) -> HumorLevel:
        return self.humor.level if self.humor else HumorLevel.NONE

    @property
    def humor_allowed(self) -> bool:
        return self.humor_level is not HumorLevel.NONE


def detect_user_style(text: str) -> str:
    """Detect the user's conversational style for tone adaptation."""
    lower = (text or "").lower()
    if any(m in lower for m in _FRUSTRATED_MARKERS):
        return "frustrated"
    if any(m in lower for m in _HURRIED_MARKERS):
        return "in_a_hurry"
    if any(m in lower for m in _JOking_MARKERS):
        return "joking"
    if any(m in lower for m in _EXPLAIN_MARKERS):
        return "asking_for_explanation"
    if len(lower) > 250 and any(t in lower for t in ("error", "traceback", "log", "stack")):
        return "technical"
    if any(m in lower for m in ("seriously", "properly", "exactly", "i need you to")):
        return "serious"
    return "casual"


def select_register(severity: Severity, tone: ToneDirective, humor_level: HumorLevel,
                    response_type: str = "general") -> str:
    """Pick which volume of the voice this response is written in.

    Deterministic, and deliberately derived from decisions the pipeline has
    already made: severity and the humor gate decide how serious the answer
    is, this decides how it sounds. A joking user cannot drag a degraded
    infrastructure answer back into casual register, and a casual question
    cannot make an outage sound relaxed.
    """
    if (severity is Severity.CRITICAL
            or tone.style == "serious"
            or humor_forbidden_response_type(response_type)):
        return "urgent"
    if (severity is Severity.ELEVATED
            or humor_level is HumorLevel.NONE
            or tone.style in ("focused", "supportive", "technical")):
        return "focused"
    return "casual"


def build_personality_directive(user_input: str,
                                state: Optional[ConversationState] = None,
                                preferences: Optional[Dict[str, str]] = None,
                                profile: Optional[PersonalityConfig] = None,
                                response_type: str = "general") -> str:
    """Produce the personality block for the system prompt.

    response_type hints at what kind of response this is (e.g.
    "diagnostic_result", "success_report", "destructive_action"). Callers
    in the control pipeline pass "destructive_action" so the humor gate
    can shut personality down around mutations.
    """
    profile = profile or get_profile()
    preferences = preferences or {}
    state = state or ConversationState()

    # Master kill-switch: when disabled, keep the default voice.
    try:
        from app.config import Config
        if not getattr(Config, "PERSONALITY_ENABLED", True):
            return ""
    except Exception:
        pass

    style = detect_user_style(user_input)
    incident_severity = ""
    if state is not None and getattr(state, "current_incident_id", None):
        # Incident state recorded by monitoring/control — conservative default.
        incident_severity = "elevated"
    severity = classify_severity(user_input, incident_severity)
    tone = select_tone(severity, style, profile.tone.verbosity, profile.tone.formality)

    # Callers that leave response_type as the generic "general" get a
    # style-derived type: casual/joking conversation is general_chat, which
    # is the humor-friendly branch of the gate. Specific response types
    # passed by the control pipeline ("destructive_action", etc.) always win.
    if response_type == "general":
        response_type = "general_chat" if style in ("casual", "joking") else "general"

    # Severity + user style can force humor off before the gate even runs.
    humor = should_use_humor(profile, severity, response_type, recent_humor_count=0)
    if not tone.humor_allowed and humor.level is not HumorLevel.NONE:
        humor = HumorDecision(HumorLevel.NONE, f"user style '{style}' — humor held back")

    return _format_directive(profile, PersonalityContext(
        profile_name="DEFAULT_JARVIS",
        severity=severity, tone=tone, humor=humor,
        user_style=style,
        register=select_register(severity, tone, humor.level, response_type),
        preferences=preferences,
        forbidden_openers=list(profile.banned_phrases),
        state_summary=state.summary() if state is not None else {},
    ))


# --------------------------------------------------------------- dialect --

# kasi (South African street) flavor. Used as seasoning, never as a costume:
# the technical content must always read like a sharp engineer wrote it, with
# slang worked into the delivery. The model chooses naturally; these are
# examples of register, not a script to insert verbatim.
_KASI_WORDS = ("aweh", "eish", "yoh", "haibo", "sharp sharp", "sure thing", "aye")


def _flavor_lines(voice: VoiceFlavorSettings, ctx: PersonalityContext) -> List[str]:
    if ctx.humor_level is HumorLevel.NONE or voice.style == "none":
        return []
    # Dialect is casual-register seasoning only. Once something is actually
    # degraded, plain speech carries the urgency better than slang — the voice
    # stays warm, the vocabulary goes straight.
    if ctx.severity is not Severity.LOW:
        return []
    if voice.style == "kasi":
        if voice.intensity >= 0.7:
            register = ("- Voice flavor: full kasi street energy — greet like a friend "
                        "(\"aweh\", \"sharp sharp\"), react with \"eish\", \"yoh\", "
                        "\"haibo\" when something's wild. Slang can appear in most "
                        "casual responses, but the technical facts stay precise and "
                        "unaffected.")
        else:
            register = ("- Voice flavor: light kasi seasoning — an occasional \"aweh\", "
                        "\"eish\", \"yoh\" or \"sharp sharp\" when it lands naturally. "
                        "Most responses stay plain; the slang is a wink, not a uniform.")
        return [
            register,
            "- Flavor discipline: at most one slang marker per response, and only where "
            "it lands naturally. Never stack them, never explain them, and never let "
            "slang stand in for the actual answer.",
            "- Flavor rules: slang affects WORDING only. Never let it touch numbers, "
            "commands, severities, or safety instructions. When severity is elevated "
            "or the user is frustrated, drop to plain speech automatically.",
        ]
    return []


def _register_lines(register: VoiceRegister, profile: PersonalityConfig,
                    ctx: PersonalityContext) -> List[str]:
    """Concrete reference phrasings for the register this response is in."""
    if ctx.tone.verbosity == "minimal":
        # A one-sentence answer has no room for example material.
        return []
    if (register.name == "casual" and profile.voice.style == "kasi"
            and profile.voice.intensity >= 0.7):
        register = VOICE_REGISTERS["casual_full"]
    lines = [
        f"- Volume dial, same voice at different volumes: {VOICE_VOLUME_DIAL}",
        f"- Reference phrasing for this response ({register.name} register) — match the "
        "register, never copy these lines verbatim:",
    ]
    lines += [f'    - "{example}"' for example in register.examples]
    lines.append(f"- Register discipline: {register.instruction}")
    return lines


def _format_directive(profile: PersonalityConfig, ctx: PersonalityContext) -> str:
    """Render the resolved decisions as compact prompt guidance."""
    register = VOICE_REGISTERS.get(ctx.register) or VOICE_REGISTERS["casual"]
    lines = [
        "How you communicate (personality — applies to wording only, never to facts, tools, or safety rules):",
        f"- You are {profile.name}, {profile.role}: {profile.character}.",
        "- One character, three volumes — never a different personality per response: warm and "
        "casual when you are just talking, sharp and focused the moment something is wrong, "
        "plain and factual when it is serious. The judgment and the facts never change; only "
        "the volume does.",
        "- Sound like the senior engineer sitting next to the user — a peer who knows this "
        "system, not a service desk reading a script. No corporate padding, no ceremony, no "
        "thanking the user for their patience.",
        f"- Current severity: {ctx.severity.value}. Current conversation style: {ctx.user_style}. "
        f"Register: {register.name} ({register.when}).",
        f"- Tone: {ctx.tone.style}; length: {ctx.tone.verbosity} ({ctx.tone.reasoning}).",
    ]

    # Response length discipline — answer as deeply as needed, no deeper.
    # Wording lives in response_style.py so verbosity stays defined once.
    lines.append(f"- {style_guidance(ctx.tone)}")

    # Humor guidance.
    if ctx.humor_level is HumorLevel.NONE:
        lines.append("- Humor: none for this response. Be direct and clear; if the situation is bad, say so plainly.")
    elif ctx.humor_level is HumorLevel.LIGHT:
        lines.append("- Humor: a light, dry aside fits here — one natural quip, worked into the answer, never at the expense of clarity.")
    elif ctx.humor_level is HumorLevel.WITTY:
        lines.append("- Humor: wit is welcome in this response. A dry observation or playful remark is encouraged — one, worked in naturally, never forced, never instead of the actual answer.")
    else:  # SARCASTIC
        lines.append("- Humor: light sarcasm is welcome here — dry, good-natured, never insulting. One beat, on top of the real answer, never instead of it.")

    # Voice flavor (Part: dialect). Wording only, and only when humor is
    # allowed at all — flavor never overrides the severity suppression.
    lines.extend(_flavor_lines(profile.voice, ctx))

    # Concrete reference phrasings for the register in play, so the voice is
    # pattern-matched from samples instead of inferred from adjectives.
    lines.extend(_register_lines(register, profile, ctx))

    # Natural voice rules.
    lines += [
        "- Talk like a sharp engineer sitting next to the user, not like a service desk. Short openers like \"Yep.\", \"Okay.\", \"Found it.\" are fine when they fit — don't force them into every message.",
        "- Match response length to the question. \"Is Docker running?\" deserves \"Yep. Docker's up.\" — not a paragraph.",
        "- Confidence must track evidence: say \"I think X is the culprit, but I want to check Y before I call it\" when evidence is thin. Never state guesses as facts.",
        "- If something is unavailable or a tool failed, say so plainly. Never present failure as success.",
        "- Don't invent logs, metrics, scores, hostnames, capabilities, or audit records. Only describe what tools actually returned.",
        "- No fake emotions (\"I feel worried\"). React through observations (\"That's ugly\", \"Well, that's unexpected\").",
        "- When the news is bad, the personality steps back: say what is broken first, plainly. "
        "No jokes, no slang, no softening — urgency outranks charm.",
        "- Never perform personality: no catchphrase at the end of every message, no slang in "
        "every reply, no nicknames. If a line would sound try-hard out loud, cut it.",
    ]

    # Banned AI-speak.
    lines.append("- Never open with or rely on generic assistant phrases such as: "
                 + "; ".join(f'"{p}"' for p in ctx.forbidden_openers) + ".")

    # Memory-driven preferences (Part 13) — soft guidance, evidence still wins.
    if ctx.preferences:
        style_pref = ctx.preferences.get("response_style")
        if style_pref:
            lines.append(f"- User preference (from memory): {style_pref}. Honor it unless the situation calls for something else.")

    # Conversation state — gives pronouns a referent.
    if ctx.state_summary:
        lines.append("- Conversation focus: " + ", ".join(f"{k}={v}" for k, v in sorted(ctx.state_summary.items())))

    return "\n".join(lines)
