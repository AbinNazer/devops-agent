"""Boundaries: what the personality layer may never do.

This module exists as an enforced contract, not just documentation.
`validate_response` is a lightweight check the quality gate runs before
a response reaches the user.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from app.personality.profile import PersonalityConfig, get_profile

# Fabrication patterns the response must not contain when no tool result
# supports them. These catch the classic hallucinated-evidence shapes.
_FABRICATION_PATTERNS = (
    (re.compile(r"\b\d+(\.\d+)?%\s*(success rate|of the time)\b", re.I), "invented success rate"),
    (re.compile(r"\brisk score (of|is|:)?\s*(about |approximately |~)?0?\.\d+", re.I), "invented risk score"),
    (re.compile(r"\bI(‘|')?ve (checked|analyzed) (the )?logs\b", re.I), "claimed unrun log inspection"),
)


@dataclass
class ResponseCheck:
    ok: bool
    violations: List[str]


def validate_response(text: str, profile: PersonalityConfig = None) -> ResponseCheck:
    """Lightweight response quality check (Part 21).

    Returns violations for: banned AI-speak openers and obvious fabricated
    statistics. This is a heuristic net — it cannot catch everything, but
    it catches the systematic failure modes the grounding work in Phases 5
    already encountered.
    """
    profile = profile or get_profile()
    violations: List[str] = []
    lowered = (text or "").lower().strip()

    for phrase in profile.banned_phrases:
        # Banned phrases are forbidden as openers/anchor phrases; a passing
        # mention inside a longer quoted sentence is acceptable.
        if lowered.startswith(phrase):
            violations.append(f"banned opener: {phrase}")
            break

    for pattern, label in _FABRICATION_PATTERNS:
        if pattern.search(text or ""):
            violations.append(label)

    return ResponseCheck(ok=not violations, violations=violations)
