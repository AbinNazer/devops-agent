"""Response style: maps resolved personality decisions to concrete
length/structure guidance. Kept separate from tone.py so presets can
override styling without touching severity logic.
"""
from __future__ import annotations

from typing import Dict

from app.personality.tone import ToneDirective

_VERBOSITY_GUIDANCE: Dict[str, str] = {
    "minimal": "One short sentence or a few words. No preamble, no summary of what you're about to do — just do it or answer it.",
    "concise": "1-3 sentences. Lead with the answer. Add one line of explanation only when it prevents a follow-up question.",
    "standard": "A short conversational paragraph or two. Cover the finding, the cause if known, and the sensible next step.",
    "detailed": "Thorough but conversational: findings, evidence, reasoning, and recommended actions. Use structure (short sections or a list) only when it genuinely aids scanning.",
}


def style_guidance(tone: ToneDirective) -> str:
    return _VERBOSITY_GUIDANCE.get(tone.verbosity, _VERBOSITY_GUIDANCE["standard"])
