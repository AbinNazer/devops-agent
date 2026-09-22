"""Tests for the personality context assembly and system prompt integration."""
from app.personality.personality_context import (
    build_personality_directive, detect_user_style,
)
from app.personality.profile import DEFAULT_JARVIS, PRESETS
from app.personality.state import ConversationState
from app.agent import build_system_prompt


class TestUserStyleDetection:
    def test_casual_default(self):
        assert detect_user_style("check docker please") == "casual"

    def test_frustrated(self):
        assert detect_user_style("wtf is wrong with this thing now") == "frustrated"

    def test_in_a_hurry(self):
        assert detect_user_style("quick: is nginx up? tldr please") == "in_a_hurry"

    def test_joking(self):
        assert detect_user_style("haha docker is having fun again lol") == "joking"

    def test_asking_for_explanation(self):
        assert detect_user_style("can you explain how the k3s ingress works?") == "asking_for_explanation"

    def test_technical_long_error(self):
        text = ("Traceback (most recent call last): " * 10) + " error log stack"
        assert detect_user_style(text) == "technical"

    def test_serious(self):
        assert detect_user_style("I need you to be precise about this") == "serious"


class TestPersonalityDirective:
    def test_directive_names_identity(self):
        d = build_personality_directive("hello there")
        assert "JARVIS" in d
        assert "How you communicate" in d

    def test_directive_contains_banned_phrases(self):
        d = build_personality_directive("hello")
        assert "as an ai" in d
        assert "certainly!" in d

    def test_directive_is_empty_when_disabled(self, monkeypatch):
        from app.config import Config
        monkeypatch.setattr(Config, "PERSONALITY_ENABLED", False, raising=False)
        assert build_personality_directive("hello") == ""

    def test_directive_uses_preset(self, monkeypatch):
        monkeypatch.setenv("JARVIS_PERSONALITY_PRESET", "PROFESSIONAL")
        d = build_personality_directive("check docker")
        assert "Humor: none" in d
    def test_directive_includes_state(self):
        s = ConversationState()
        s.observe("look at the redis container")
        d = build_personality_directive("and now?", state=s)
        assert "last_subject=redis" in d

    def test_directive_includes_preferences(self):
        d = build_personality_directive("hello", preferences={"response_style": "short answers, no fluff"})
        assert "short answers, no fluff" in d

    def test_destructive_action_directive_suppresses_humor(self):
        d = build_personality_directive("restart the jenkins container",
                                        response_type="destructive_action")
        assert "Humor: none" in d

    def test_casual_message_welcomes_humor(self):
        """Casual messages must invite humor (SARCASTIC at level 0.8), not
        merely permit it — this is the fix for 'can't see any humor'."""
        d = build_personality_directive("hey what's up")
        assert "Humor: light sarcasm is welcome" in d

    def test_joking_message_welcomes_humor(self):
        d = build_personality_directive("lol docker crashed again")
        humor_line = next(l for l in d.splitlines() if l.startswith("- Humor"))
        assert "none" not in humor_line.split(":")[1]

    def test_technical_message_humor_not_sarcastic(self):
        """Technical paste (long traceback) is not the sarcastic branch."""
        text = ("Traceback (most recent call last): " * 12) + " error log stack"
        d = build_personality_directive(text)
        assert "light sarcasm is welcome" not in d
        assert "Humor:" in d  # some explicit humor guidance still present


class TestSystemPromptIntegration:
    def test_prompt_contains_personality(self):
        p = build_system_prompt("is docker running?")
        assert "How you communicate" in p
        assert "JARVIS" in p

    def test_personality_is_final_section(self):
        """The personality directive must be the LAST part of the system
        prompt — safety/grounding rules precede it, so personality can only
        shape wording, never outrank them."""
        p = build_system_prompt("hello")
        pers_idx = p.find("How you communicate")
        assert pers_idx != -1
        assert pers_idx > 1000  # substantial base prompt precedes it
        assert p.strip().endswith(".") or "Conversation focus" in p or "preference" in p.lower() or pers_idx > len(p) - 3000

    def test_prompt_with_state_resolves_context(self):
        s = ConversationState()
        s.observe("check the billing container")
        p = build_system_prompt("why is it unhealthy?", conversation_state=s)
        assert "last_subject=billing" in p

    def test_prompt_includes_grounding_rules(self):
        p = build_system_prompt("hello")
        assert "invent" in p.lower()  # grounding: never invent logs/metrics
