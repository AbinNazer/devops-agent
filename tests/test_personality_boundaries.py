"""Tests for response boundaries, the quality gate, and memory preferences
integration."""
from app.personality.boundaries import validate_response
from app.personality.preferences_bridge import load_communication_preferences
from app.personality.profile import DEFAULT_JARVIS


class TestBannedAISpeak:
    def test_certainly_banned(self):
        r = validate_response("Certainly! I'll check that for you.", DEFAULT_JARVIS)
        assert not r.ok
        assert any("banned" in v for v in r.violations)

    def test_as_an_ai_banned(self):
        r = validate_response("As an AI assistant, I cannot do that.", DEFAULT_JARVIS)
        assert not r.ok

    def test_apology_formula_banned(self):
        r = validate_response("I apologize for the inconvenience. Let me try again.", DEFAULT_JARVIS)
        assert not r.ok

    def test_happy_to_banned(self):
        r = validate_response("I'd be happy to help you with that!", DEFAULT_JARVIS)
        assert not r.ok

    def test_mid_sentence_passing_mention_ok(self):
        """A quoted/brief mention inside a longer sentence is acceptable —
        the ban targets anchoring on the phrase."""
        r = validate_response("The docs say 'certainly' is overused, so I avoid it.", DEFAULT_JARVIS)
        assert r.ok


class TestNaturalVoiceAllowed:
    def test_short_openers_allowed(self):
        for text in ("Yep. Docker's up.", "Okay, checking.", "Found it.",
                     "Hmm. That's not healthy.", "Got it."):
            r = validate_response(text, DEFAULT_JARVIS)
            assert r.ok, f"{text}: {r.violations}"

    def test_plain_failure_statement_allowed(self):
        r = validate_response("Couldn't reach Docker on the server. I haven't verified the container state yet.", DEFAULT_JARVIS)
        assert r.ok


class TestFabricationChecks:
    def test_invented_success_rate_flagged(self):
        r = validate_response("Restart has a 95% success rate of the time.", DEFAULT_JARVIS)
        assert not r.ok
        assert any("success rate" in v for v in r.violations)

    def test_invented_risk_score_flagged(self):
        r = validate_response("The risk score is approximately 0.45 for this action.", DEFAULT_JARVIS)
        assert not r.ok

    def test_real_tool_output_not_flagged(self):
        r = validate_response("CPU is at 91% right now — that's the issue.", DEFAULT_JARVIS)
        assert r.ok


class TestMemoryPreferencesBridge:
    def test_none_repository_returns_empty(self):
        assert load_communication_preferences(None) == {}

    def test_broken_repository_returns_empty(self):
        class Boom:
            def anything(self):
                raise RuntimeError("db down")

        # get_preferences must not blow up the response path
        result = load_communication_preferences(Boom())
        assert isinstance(result, dict)

    def test_returns_only_style_keys(self):
        from unittest.mock import patch

        class FakeRepo:
            pass

        with patch("app.personality.preferences_bridge.get_preferences",
                   return_value={"response_style": "casual", "home_address": "x", "format": "bullets"}):
            prefs = load_communication_preferences(FakeRepo())
        assert prefs == {"response_style": "casual", "format": "bullets"}
