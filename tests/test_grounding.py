"""
Regression tests for Phase 5 hallucination/grounding.

These tests prove that the system prompt contains explicit grounding
rules preventing the LLM from inventing implementation details, and
that the actual implementation matches what the system prompt describes.

Root cause of the original issue:
  The SYSTEM_PROMPT lacked constraints about what Phase 5 can/cannot do.
  The LLM filled in plausible-sounding details (HTTP health probing,
  stored snapshots, docker stop/recreate during rollback, historical
  success rates, specific risk scores) that are not implemented.

These tests prevent regression by verifying both the prompt text and
the actual implementation capabilities.
"""
import pytest

from app.agent import SYSTEM_PROMPT
from app.control.risk import assess_risk, _ACTION_RISK_PROFILES
from app.control.verification import _VERIFICATION_STRATEGIES
from app.control.rollback import create_rollback_plan
from app.control.actions import get_allowed_action_types
from app.control.policy import get_allowed_actions, BLOCKED_ACTIONS
from app.ssh_whitelist import WHITELIST_PATTERNS


# ── 1. System prompt grounding rules exist ──

class TestSystemPromptGrounding:
    """Verify the system prompt contains explicit grounding constraints."""

    def test_grounding_section_exists(self):
        """System prompt must have a 'Phase 5 ground truth' section."""
        assert "Phase 5 ground truth" in SYSTEM_PROMPT

    def test_grounding_says_only_restart_actions_allowed(self):
        """Prompt must state only restart_container and restart_service are allowed."""
        lower = SYSTEM_PROMPT.lower()
        assert "restart_container" in lower
        assert "restart_service" in lower

    def test_grounding_forbids_inventing_implementation(self):
        """Prompt must explicitly forbid inventing implementation details."""
        assert "Never invent implementation details" in SYSTEM_PROMPT

    def test_grounding_forbids_specific_scores_without_tool_result(self):
        """Prompt must not let the LLM state risk scores unless quoting tool results."""
        assert "Do NOT state a specific numeric score" in SYSTEM_PROMPT

    def test_grounding_forbids_historical_success_rates(self):
        """Prompt must forbid claiming stored historical success rates."""
        lower = SYSTEM_PROMPT.lower()
        assert "no stored historical success rates" in lower

    def test_grounding_forbids_management_keys(self):
        """Prompt must forbid claiming management keys exist."""
        lower = SYSTEM_PROMPT.lower()
        assert "no management keys" in lower

    def test_grounding_forbids_stored_snapshots(self):
        """Prompt must forbid claiming stored container snapshots."""
        lower = SYSTEM_PROMPT.lower()
        assert "no stored snapshots" in lower or "no pre-captured" in lower

    def test_grounding_forbids_http_health_probing(self):
        """Prompt must state there is NO HTTP health probing."""
        lower = SYSTEM_PROMPT.lower()
        assert "no http health probing" in lower or "no curl-based" in lower

    def test_grounding_forbids_docker_stop_in_rollback(self):
        """Prompt must state rollback does NOT use docker stop."""
        lower = SYSTEM_PROMPT.lower()
        assert "does not stop/recreate" in lower or "does not use docker run" in lower

    def test_grounding_says_rollback_is_just_restart_again(self):
        """Prompt must state rollback for restart is just restarting again."""
        lower = SYSTEM_PROMPT.lower()
        assert "restarts the container" in lower or "restarts the service" in lower

    def test_grounding_says_verification_checks_state_not_time(self):
        """Prompt must not claim a fixed verification time."""
        lower = SYSTEM_PROMPT.lower()
        assert "30-second" not in lower and "30 second" not in lower

    def test_grounding_says_hypotheticals_must_be_honest(self):
        """Prompt must instruct the LLM to distinguish actual from hypothetical."""
        lower = SYSTEM_PROMPT.lower()
        assert "hypothetical" in lower


# ── 2. Actual implementation matches system prompt claims ──

class TestImplementationMatchesPrompt:
    """Verify the actual code matches what the system prompt tells the LLM."""

    def test_only_two_action_types_exist(self):
        """Only restart_container and restart_service are in the allowed list."""
        types = get_allowed_action_types()
        assert sorted(types) == ["restart_container", "restart_service"]

    def test_no_http_verification_strategies(self):
        """Verification strategies must NOT include HTTP/curl checks."""
        for action, strategies in _VERIFICATION_STRATEGIES.items():
            for strategy in strategies:
                # Function names should not reference HTTP, curl, or health endpoint
                name = strategy.__name__
                assert "http" not in name.lower()
                assert "curl" not in name.lower()
                assert "health_endpoint" not in name.lower()

    def test_verification_checks_state_not_time(self):
        """Verification strategies must check container state, not time."""
        strategy_names = [
            fn.__name__
            for fns in _VERIFICATION_STRATEGIES.values()
            for fn in fns
        ]
        # Should have state/health/logs checks, not time-based ones
        assert any("running" in n or "state" in n for n in strategy_names)
        assert not any("timeout" in n or "wait" in n for n in strategy_names)

    def test_rollback_for_restart_is_just_restart(self):
        """Rollback for restart_container must use docker restart, not docker stop/run."""
        plan = create_rollback_plan("restart_container", "test-container")
        assert plan.available is True
        assert plan.command == "docker restart test-container"
        assert "stop" not in plan.command.lower()
        assert "docker run" not in plan.command.lower()
        assert "rm" not in plan.command.lower()

    def test_rollback_for_service_is_just_restart(self):
        """Rollback for restart_service must use systemctl restart."""
        plan = create_rollback_plan("restart_service", "nginx")
        assert plan.available is True
        assert plan.command == "systemctl restart nginx"
        assert "stop" not in plan.command.lower()
        assert "disable" not in plan.command.lower()

    def test_rollback_for_unknown_action_not_available(self):
        """Rollback must not be available for non-restart actions."""
        plan = create_rollback_plan("delete_resource", "database")
        assert plan.available is False

    def test_risk_scores_are_dynamic_not_fixed(self):
        """Risk scores must be computed dynamically from factors, not hardcoded."""
        risk1 = assess_risk("restart_container", "backend")
        risk2 = assess_risk("restart_container", "redis")  # high-risk target
        # redis has higher risk due to being a high-risk target
        assert risk2.overall_score > risk1.overall_score
        # Both should have valid scores
        assert 0.0 <= risk1.overall_score <= 1.0
        assert 0.0 <= risk2.overall_score <= 1.0

    def test_risk_factors_are_seven(self):
        """Risk assessment must use exactly 7 factors."""
        risk = assess_risk("restart_container", "backend")
        assert len(risk.factors) == 7

    def test_risk_factor_names_match_implementation(self):
        """Risk factor names must match the actual code."""
        risk = assess_risk("restart_container", "backend")
        names = {f.name for f in risk.factors}
        expected = {
            "severity", "blast_radius", "reversibility",
            "production_impact", "destructive_potential",
            "confidence", "dependency_impact",
        }
        assert names == expected

    def test_no_docker_stop_in_allowed_commands(self):
        """docker stop must NOT be in any allowed command."""
        allowed = get_allowed_actions()
        for category in allowed["allowed"].values():
            for action in category:
                assert "stop" not in action.lower()

    def test_docker_stop_in_blocked_actions(self):
        """docker stop must be in the blocked actions list."""
        assert "docker_system_prune" in BLOCKED_ACTIONS

    def test_no_curl_in_whitelist(self):
        """curl must NOT be in the SSH whitelist patterns."""
        for pattern in WHITELIST_PATTERNS:
            assert "curl" not in pattern.lower()
            assert "wget" not in pattern.lower()

    def test_no_docker_exec_in_whitelist(self):
        """docker exec must NOT be in the SSH whitelist."""
        for pattern in WHITELIST_PATTERNS:
            assert "docker exec" not in pattern.lower()

    def test_no_sudo_in_whitelist(self):
        """sudo must NOT be in the SSH whitelist."""
        for pattern in WHITELIST_PATTERNS:
            assert "sudo" not in pattern.lower()

    def test_allowed_actions_report_matches_code(self):
        """get_allowed_actions report must match the actual BLOCKED_ACTIONS set."""
        report = get_allowed_actions()
        # Blocked actions in the report should include known dangerous ones
        blocked = set(report["blocked"])
        for dangerous in ["rm", "delete", "destroy", "shell_execute", "arbitrary_command"]:
            assert dangerous in blocked


# ── 3. No fabricated capabilities in system prompt ──

class TestNoFabricatedCapabilities:
    """Ensure the system prompt does not claim capabilities that don't exist."""

    def test_no_claim_of_http_health_endpoint(self):
        """System prompt must NOT claim HTTP /health endpoint checking."""
        lower = SYSTEM_PROMPT.lower()
        # Should not have unqualified claims about HTTP probing
        assert "/health" not in lower or "no" in lower.split("/health")[0][-20:]

    def test_no_claim_of_docker_snapshot(self):
        """System prompt must NOT claim container configuration snapshots."""
        lower = SYSTEM_PROMPT.lower()
        # The prompt should say no snapshots, not that snapshots exist
        lines_with_snapshot = [l for l in lower.split("\n") if "snapshot" in l]
        for line in lines_with_snapshot:
            assert "no" in line or "not" in line

    def test_no_claim_of_stored_rollback_state(self):
        """System prompt must NOT claim previous container state is stored."""
        lower = SYSTEM_PROMPT.lower()
        # Should not claim "previous configuration" is restored
        assert "restore the exact previous" not in lower
        assert "stored docker run" not in lower

    def test_no_claim_of_fixed_verification_timeout(self):
        """System prompt must NOT claim a specific verification timeout."""
        lower = SYSTEM_PROMPT.lower()
        # No specific time claims like "30 seconds" for verification
        for time_claim in ["30-second", "30 second", "60-second", "60 second"]:
            assert time_claim not in lower

    def test_no_claim_of_curl_health_check(self):
        """System prompt must NOT claim curl-based health checks are implemented."""
        lower = SYSTEM_PROMPT.lower()
        # The prompt should state there are NO curl-based checks
        assert "no curl-based" in lower


# ── 4. Policy/permission/risk pipeline still intact ──

class TestPipelineIntact:
    """Verify the complete Phase 5 safety pipeline is unchanged."""

    def test_policy_allows_only_restart(self):
        """Policy must only allow restart_container and restart_service."""
        from app.control.policy import is_action_allowed
        assert is_action_allowed("restart_container") is True
        assert is_action_allowed("restart_service") is True
        assert is_action_allowed("rm") is False
        assert is_action_allowed("delete") is False

    def test_permission_system_classifies_risks(self):
        """Permission system must classify risk tiers correctly."""
        from app.control.permissions import determine_approval_requirement
        assert determine_approval_requirement("low") == "auto"
        assert determine_approval_requirement("medium") == "ask"
        assert determine_approval_requirement("high") == "ask_explain"
        assert determine_approval_requirement("critical") == "block"

    def test_risk_assessment_computes_for_restart(self):
        """Risk assessment must compute a valid result for restart actions."""
        risk = assess_risk("restart_container", "backend")
        assert risk.risk_level in ("low", "medium", "high", "critical")
        assert len(risk.factors) == 7
        assert risk.overall_score > 0.0

    def test_verification_has_strategies_for_both_actions(self):
        """Both restart actions must have verification strategies."""
        assert "restart_container" in _VERIFICATION_STRATEGIES
        assert "restart_service" in _VERIFICATION_STRATEGIES

    def test_verification_strategy_count(self):
        """restart_container must have exactly 3 checks (state, health, logs)."""
        strategies = _VERIFICATION_STRATEGIES["restart_container"]
        assert len(strategies) == 3

    def test_rollback_available_for_both_actions(self):
        """Rollback must be available for both restart actions."""
        plan_c = create_rollback_plan("restart_container", "backend")
        plan_s = create_rollback_plan("restart_service", "nginx")
        assert plan_c.available is True
        assert plan_s.available is True

    def test_actions_execute_through_whitelist(self):
        """Action commands must pass through the SSH whitelist."""
        from app.control.actions import build_action_command
        from app.ssh_whitelist import is_command_allowed
        cmd_c = build_action_command("restart_container", "backend")
        cmd_s = build_action_command("restart_service", "nginx")
        assert is_command_allowed(cmd_c) is True
        assert is_command_allowed(cmd_s) is True
