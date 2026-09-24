"""JARVIS personality subsystem.

Influences HOW the agent communicates — never WHETHER an operation is
allowed, and never WHAT the technical facts are. The personality layer
sits after reasoning/tool execution in the pipeline and can only shape
tone, verbosity, humor, and phrasing guidance given to the LLM.
"""
from app.personality.profile import (
    PersonalityConfig, DEFAULT_JARVIS, PRESETS, VOICE_REGISTERS, VOICE_VOLUME_DIAL, get_profile,
)
from app.personality.state import ConversationState, get_conversation_state, reset_conversation_state
from app.personality.personality_context import (
    build_personality_directive, select_register, PersonalityContext,
)

__all__ = [
    "PersonalityConfig",
    "DEFAULT_JARVIS",
    "PRESETS",
    "VOICE_REGISTERS",
    "VOICE_VOLUME_DIAL",
    "get_profile",
    "ConversationState",
    "get_conversation_state",
    "reset_conversation_state",
    "build_personality_directive",
    "select_register",
    "PersonalityContext",
]
