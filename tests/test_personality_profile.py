"""Tests for the personality profile, presets, and configuration selection."""
import pytest

from app.personality.profile import (
    DEFAULT_JARVIS, PRESETS, PersonalityConfig, get_profile,
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
        for name in ("DEFAULT_JARVIS", "PROFESSIONAL", "CASUAL", "MINIMAL", "INCIDENT_MODE"):
            assert name in PRESETS

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

    def test_casual_chat_gets_humor(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "general_chat")
        assert d.level in (HumorLevel.LIGHT, HumorLevel.WITTY)

    def test_elevated_caps_at_light(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.ELEVATED, "general_chat")
        assert d.level is HumorLevel.LIGHT

    def test_frequency_damping(self):
        d = should_use_humor(DEFAULT_JARVIS, Severity.LOW, "general_chat", recent_humor_count=2)
        assert d.level in (HumorLevel.LIGHT, HumorLevel.NONE)
        assert "damping" in d.reasoning

    def test_disabled_profile_never_humor(self):
        d = should_use_humor(PRESETS["PROFESSIONAL"], Severity.LOW, "general_chat")
        assert d.level is HumorLevel.NONE
