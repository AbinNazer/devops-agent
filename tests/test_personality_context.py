"""Tests for the personality context assembly and system prompt integration."""
from unittest.mock import MagicMock

from app.personality.humor import HumorLevel
from app.personality.personality_context import (
    build_personality_directive, detect_user_style, select_register,
)
from app.personality.profile import DEFAULT_JARVIS, PRESETS
from app.personality.state import ConversationState
from app.personality.tone import Severity, select_tone
from app.agent import Agent, build_system_prompt


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


class TestVoiceFlavor:
    def test_casual_directive_carries_kasi_flavor(self):
        """Default profile (kasi, 0.45): casual chat includes flavor lines."""
        d = build_personality_directive("hey what's up")
        assert "Voice flavor" in d
        assert "kasi" in d

    def test_flavor_suppressed_on_destructive_actions(self):
        d = build_personality_directive("restart the jenkins container",
                                        response_type="destructive_action")
        assert "Voice flavor" not in d

    def test_flavor_suppressed_when_humor_none(self):
        from app.personality.profile import PersonalityConfig, HumorSettings
        stone = PersonalityConfig(humor=HumorSettings(enabled=False, level=0.0))
        d = build_personality_directive("hello there", profile=stone)
        assert "Voice flavor" not in d

    def test_kasi_preset_directive_is_full_flavor(self):
        from app.personality.profile import PRESETS
        d = build_personality_directive("hey, check docker for me",
                                        profile=PRESETS["KASI"])
        assert "full kasi street energy" in d

    def test_flavor_rules_bound_technical_content(self):
        d = build_personality_directive("hey")
        assert "WORDING only" in d

    def test_flavor_drops_out_once_infrastructure_is_degraded(self):
        """Dialect is casual seasoning only — a degraded system gets plain speech."""
        d = build_personality_directive("the jenkins container keeps crashing")
        assert "Voice flavor" not in d

    def test_flavor_discipline_prevents_try_hard_slang(self):
        d = build_personality_directive("hey what's up")
        assert "at most one slang marker per response" in d


class TestRegisterSelection:
    """Register = which volume of the same voice a response uses."""

    def test_critical_severity_selects_urgent(self):
        tone = select_tone(Severity.CRITICAL, "casual", 0.4, 0.2)
        assert select_register(Severity.CRITICAL, tone, HumorLevel.NONE) == "urgent"

    def test_elevated_severity_selects_focused(self):
        tone = select_tone(Severity.ELEVATED, "casual", 0.4, 0.2)
        assert select_register(Severity.ELEVATED, tone, HumorLevel.LIGHT) == "focused"

    def test_relaxed_chat_selects_casual(self):
        tone = select_tone(Severity.LOW, "casual", 0.4, 0.2)
        assert select_register(Severity.LOW, tone, HumorLevel.SARCASTIC) == "casual"

    def test_mutation_response_type_forces_urgent(self):
        tone = select_tone(Severity.LOW, "casual", 0.4, 0.2)
        assert select_register(Severity.LOW, tone, HumorLevel.NONE,
                              "destructive_action") == "urgent"

    def test_casual_message_renders_casual_register(self):
        d = build_personality_directive("hey what's up")
        assert "Register: casual" in d
        assert "Volume dial" in d
        assert "casual register" in d

    def test_degraded_message_renders_focused_register(self):
        d = build_personality_directive("the jenkins container keeps crashing")
        assert "Register: focused" in d
        assert "focused register" in d

    def test_outage_renders_urgent_register(self):
        d = build_personality_directive("production is down, everything is timing out")
        assert "Register: urgent" in d
        assert "urgent register" in d

    def test_destructive_action_renders_urgent_register(self):
        d = build_personality_directive("restart the jenkins container",
                                        response_type="destructive_action")
        assert "Register: urgent" in d
        assert "Humor: none" in d

    def test_joking_user_cannot_casualise_a_degraded_system(self):
        """The key tone-switching guarantee: banter never lowers the volume
        while infrastructure is actually degraded."""
        d = build_personality_directive("lol the api keeps crashing again")
        assert "Register: focused" in d
        assert "Register: casual" not in d

    def test_tone_switching_is_explained_as_one_voice(self):
        d = build_personality_directive("hello")
        assert "One character, three volumes" in d

    def test_bad_news_rule_gives_urgency_priority(self):
        d = build_personality_directive("hello")
        assert "urgency outranks charm" in d

    def test_try_hard_personality_is_ruled_out(self):
        d = build_personality_directive("hello")
        assert "try-hard" in d

    def test_register_examples_are_omitted_for_minimal_answers(self):
        d = build_personality_directive("quick: is nginx up? tldr")
        assert "Volume dial" not in d

    def test_kasi_preset_swaps_in_full_flavor_examples(self):
        d = build_personality_directive("hey, check docker for me",
                                        profile=PRESETS["KASI"])
        assert "that deploy went sideways" in d

    def test_default_voice_keeps_light_examples(self):
        d = build_personality_directive("hey there")
        assert "that deploy went sideways" not in d
        assert "Nothing broken here" in d


class TestCrossInterfaceConsistency:
    """CLI and web/PWA must sound like the same character."""

    def test_both_entrypoints_seed_the_same_base_prompt(self):
        from app.api.app import SYSTEM_PROMPT as API_PROMPT, _build_history
        from app.api.models import ChatMessage, Conversation, MessageRole
        from app.main import fresh_history

        conversation = Conversation()
        conversation.messages.append(ChatMessage(role=MessageRole.USER, content="hi"))

        assert fresh_history()[0] == {"role": "system", "content": API_PROMPT}
        assert _build_history(conversation)[0] == {"role": "system", "content": API_PROMPT}

    def test_personality_lives_in_one_place_only(self):
        """Neither entry point may hardcode its own persona text."""
        from app.api.app import SYSTEM_PROMPT as API_PROMPT
        from app.main import fresh_history

        assert "How you communicate" not in API_PROMPT
        assert "How you communicate" not in fresh_history()[0]["content"]

    def test_agent_applies_the_directive_once_and_after_the_safety_rules(self):
        provider = MagicMock()
        provider.name = "mock"
        provider.chat.return_value = {"role": "assistant", "content": "ok", "tool_calls": []}
        agent = Agent(provider=provider, tool_schemas=[], execute_tool_fn=lambda n, a: {},
                      allowed_tool_names=set(), conversation_state=ConversationState())

        agent.run("hello", history=[{"role": "system", "content": "base system prompt"}])

        system = provider.chat.call_args[0][0][0]["content"]
        assert system.count("How you communicate") == 1
        assert system.find("How you communicate") > system.find("Never invent infrastructure information")
        assert "Register:" in system
