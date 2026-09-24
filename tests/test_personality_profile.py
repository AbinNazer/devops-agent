"""Tests for the personality profile, presets, and configuration selection."""
import pytest

from app.personality.profile import (
    DEFAULT_JARVIS, PRESETS, VOICE_REGISTERS, VOICE_VOLUME_DIAL,
    PersonalityConfig, get_profile,
)
from app.personality.tone import Severity, classify_severity, select_tone
from app.personality.humor import HumorLevel, should_use_humor


class TestProfile:
    def test_default_profile_traits(self):
        p = DEFAULT_JARVIS
        assert p.name == "JARVIS"
        assert p.humor.enabled is True
        assert 0.0 < p.tone.formality < 0.5  # casual-leaning
        assert p.behavior.admit_uncertainty is True

    def test_presets_exist(self):
        for name in ("DEFAULT_JARVIS", "PROFESSIONAL", "CASUAL", "KASI", "MINIMAL", "INCIDENT_MODE"):
            assert name in PRESETS

    def test_default_voice_is_kasi(self):
        """The default JARVIS voice carries light kasi seasoning."""
        assert DEFAULT_JARVIS.voice.style == "kasi"
        assert 0.3 <= DEFAULT_JARVIS.voice.intensity <= 0.6

    def test_kasi_preset_is_full_flavor(self):
        p = PRESETS["KASI"]
        assert p.voice.style == "kasi"
        assert p.voice.intensity >= 0.7
        assert p.humor.enabled is True and p.humor.level >= 0.9

    def test_incident_mode_disables_humor(self):
        p = PRESETS["INCIDENT_MODE"]
        assert p.humor.enabled is False

    def test_get_profile_default(self, monkeypatch):
        monkeypatch.delenv("JARVIS_PERSONALITY_PRESET", raising=False)
        assert get_profile() is DEFAULT_JARVIS

    def test_get_profile_preset_selection(self, monkeypatch):
        monkeypatch.setenv("JARVIS_PERSONALITY_PRESET", "PROFESSIONAL")
        assert get_profile() is PRESETS["PROFESSIONAL"]

    def test_get_profile_unknown_preset_falls_back(self, monkeypatch):
        monkeypatch.setenv("JARVIS_PERSONALITY_PRESET", "NOT_A_PRESET")
        assert get_profile() is DEFAULT_JARVIS


class TestSeverity:
    def test_casual_message_is_low(self):
        assert classify_severity("hey what's up") is Severity.LOW

    def test_outage_is_critical(self):
        assert classify_severity("production is down, help") is Severity.CRITICAL
        assert classify_severity("we have a security breach") is Severity.CRITICAL

    def test_degradation_is_elevated(self):
        assert classify_severity("the jenkins container keeps crashing") is Severity.ELEVATED
        assert classify_severity("nginx is not responding") is Severity.ELEVATED

    def test_incident_state_overrides(self):
        assert classify_severity("any text", incident_severity="critical") is Severity.CRITICAL
        assert classify_severity("any text", incident_severity="high") is Severity.CRITICAL

    def test_empty_text_is_low(self):
        assert classify_severity("") is Severity.LOW


class TestTone:
    def test_critical_forces_serious_detailed(self):
        t = select_tone(Severity.CRITICAL, "casual", 0.4, 0.2)
        assert t.style == "serious"
        assert t.verbosity == "detailed"
        assert t.humor_allowed is False

    def test_frustrated_user_gets_supportive(self):
        t = select_tone(Severity.ELEVATED, "frustrated", 0.4, 0.2)
        assert t.style == "supportive"
        assert t.humor_allowed is False

    def test_casual_low_severity_stays_casual(self):
        t = select_tone(Severity.LOW, "casual", 0.4, 0.2)
        assert t.style == "casual"
        assert t.humor_allowed is True

    def test_in_a_hurry_gets_minimal(self):
        t = select_tone(Severity.LOW, "in_a_hurry", 0.4, 0.2)
        assert t.verbosity == "minimal"

    def test_asking_for_explanation_gets_detailed(self):
        t = select_tone(LOW := Severity.LOW, "asking_for_explanation", 0.4, 0.2)
        assert t.verbosity == "detailed"

    def test_elevated_forces_focused_tone(self):
        """Degraded infrastructure raises the volume no matter how relaxed
        the user sounds — a casual message must not keep the response casual
        while something is actually broken."""
        t = select_tone(Severity.ELEVATED, "casual", 0.4, 0.2)
        assert t.style == "focused"
        assert t.verbosity == "concise"
        assert t.humor_allowed is True

    def test_elevated_joking_user_still_gets_focused(self):
        """A joke must not talk a degraded system back down to casual."""
        t = select_tone(Severity.ELEVATED, "joking", 0.4, 0.2)
        assert t.style == "focused"

    def test_elevated_hurried_user_stays_focused_but_minimal(self):
        t = select_tone(Severity.ELEVATED, "in_a_hurry", 0.4, 0.2)
        assert t.style == "focused"
        assert t.verbosity == "minimal"

    def test_elevated_frustrated_user_still_supportive(self):
        t = select_tone(Severity.ELEVATED, "frustrated", 0.4, 0.2)
        assert t.style == "supportive"
        assert t.humor_allowed is False


class TestVoiceRegisters:
    """The voice is one character at three volumes, defined once as data."""

    def test_every_volume_is_defined(self):
        for key in ("casual", "casual_full", "focused", "urgent"):
            assert key in VOICE_REGISTERS

    def test_every_register_ships_reference_phrasings(self):
        for key, register in VOICE_REGISTERS.items():
            assert len(register.examples) >= 2, f"{key} needs examples to pattern-match"
            assert register.instruction, f"{key} needs register discipline"
            assert register.when, f"{key} needs a situation"

    def test_urgent_register_models_no_banter(self):
        """The urgent register must never demonstrate slang or jokes."""
        urgent = VOICE_REGISTERS["urgent"]
        text = " ".join(urgent.examples + (urgent.instruction,)).lower()
        for marker in ("aweh", "eish", "yoh", "haibo", "lol", "haha"):
            assert marker not in text

    def test_volume_dial_demonstrates_the_tone_switch(self):
        assert "casual:" in VOICE_VOLUME_DIAL
        assert "outage:" in VOICE_VOLUME_DIAL

    def test_full_flavor_examples_differ_from_light_ones(self):
        assert VOICE_REGISTERS["casual_full"].examples != VOICE_REGISTERS["casual"].examples
        assert VOICE_REGISTERS["casual_full"].name == VOICE_REGISTERS["casual"].name


class TestHumorGate:
    def test_critical_never_humor(self):
        p = DEFAULT_JARVIS
        for rtype in ("general_chat", "diagnostic_result", "success_report", "acknowledgment"):
            d = should_use_humor(p, Severity.CRITICAL, rtype)
            assert d.level is HumorLevel.NONE, f"{rtype}: {d.reasoning}"

    def test_destructive_action_never_humor(self):
        p = DEFAULT_JARVIS
        for sev in (Severity.LOW, Severity.ELEVATED):
            d = should_use_humor(p, sev, "destructive_action")
            assert d.level is HumorLevel.NONE

    def test_security_never_humor(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "security")
        assert d.level is HumorLevel.NONE

    def test_approval_request_never_humor(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "approval_request")
        assert d.level is HumorLevel.NONE

    def test_failure_report_never_humor(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "failure_report")
        assert d.level is HumorLevel.NONE

    def test_casual_chat_gets_sarcastic(self):
        """Default profile (humor level 0.8): casual chat unlocks sarcasm."""
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "general_chat")
        assert d.level is HumorLevel.SARCASTIC

    def test_neutral_context_still_gets_witty(self):
        """Generic 'general' response type keeps a witty default voice."""
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "general")
        assert d.level is HumorLevel.WITTY

    def test_low_profile_level_gets_witty_not_sarcastic(self):
        """Profiles below the 0.7 threshold get WITTY, not SARCASTIC."""
        from app.personality.profile import PersonalityConfig, HumorSettings
        shy = PersonalityConfig(humor=HumorSettings(enabled=True, level=0.5))
        d = should_use_humor(shy, Severity.LOW, "general_chat")
        assert d.level is HumorLevel.WITTY

    def test_elevated_caps_at_light(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.ELEVATED, "general_chat")
        assert d.level is HumorLevel.LIGHT

    def test_frequency_damping(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "general_chat", recent_humor_count=4)
        assert d.level in (HumorLevel.LIGHT, HumorLevel.NONE)
        assert "damping" in d.reasoning

    def test_damping_threshold_tolerance(self):
        """Three humorous responses in a row does not yet trigger damping."""
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "general_chat", recent_humor_count=3)
        assert d.level is HumorLevel.SARCASTIC

    def test_disabled_profile_never_humor(self):
        d = should_use_humor(PRESETS["PROFESSIONAL"], Severity.LOW, "general_chat")
        assert d.level is HumorLevel.NONE
