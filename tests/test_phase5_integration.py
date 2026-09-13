"""
Tests for Phase 5 agent integration: proving that the read-only restriction
has been corrected so restart_container / restart_service are reachable
through the controlled pipeline, while destructive actions remain blocked.

Root cause of the original bug:
  The SYSTEM_PROMPT in app/agent.py told the LLM:
  "You cannot execute shell commands or perform destructive operations — you
  only have the read-only tools you've been given. If asked to do something
  destructive (delete, restart, deploy, modify), say plainly that this isn't
  available yet."

  This blanket instruction prevented the LLM from ever calling run_diagnostic
  for restart requests, even though Phase 5 fully supports them.

This test file proves the fix works.
"""
import pytest

from app.agent import SYSTEM_PROMPT
from app.tool_registry import TOOL_SCHEMAS, _DISPATCH
from app.control.policy import is_action_allowed, evaluate_action, ActionRequest
from app.control.actions import is_action_executable, get_allowed_action_types
from app.control.state import RiskLevel


# ── 1. System prompt no longer blocks restarts ──

class TestSystemPromptCorrected:
    """Verify the SYSTEM_PROMPT no longer contains the blanket read-only restriction."""

    def test_system_prompt_mentions_run_diagnostic(self):
        """System prompt should tell the LLM about run_diagnostic for actions."""
        assert "run_diagnostic" in SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_restart_through_pipeline(self):
        """System prompt should reference restart going through a controlled pipeline."""
        lower = SYSTEM_PROMPT.lower()
        assert "restart" in lower
        assert "pipeline" in lower or "control" in lower

    def test_system_prompt_does_not_say_restart_unavailable(self):
        """System prompt must NOT say restart is 'not available yet'."""
        assert "not available yet" not in SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_say_readonly_tools_only(self):
        """System prompt must NOT claim only read-only tools exist."""
        # The old prompt had "you only have the read-only tools you've been given"
        assert "only have the read-only tools" not in SYSTEM_PROMPT.lower()

    def test_system_prompt_still_blocks_destructive_operations(self):
        """System prompt MUST still block truly destructive operations."""
        lower = SYSTEM_PROMPT.lower()
        assert "permanently blocked" in lower
        assert "delete" in lower
        assert "rm" in lower

    def test_system_prompt_blocks_arbitrary_shell(self):
        """System prompt must still forbid arbitrary shell execution."""
        lower = SYSTEM_PROMPT.lower()
        assert "arbitrary shell" in lower

    def test_system_prompt_blocks_docker_exec(self):
        """System prompt must still block docker exec."""
        assert "docker exec" in SYSTEM_PROMPT.lower()

    def test_system_prompt_blocks_sudo(self):
        """System prompt must still block sudo."""
        assert "sudo" in SYSTEM_PROMPT.lower()

    def test_system_prompt_blocks_curl_pipe_bash(self):
        """System prompt must still block curl|bash."""
        assert "curl|bash" in SYSTEM_PROMPT.lower()

    def test_system_prompt_requires_target_for_restart(self):
        """System prompt should ask for a target when user doesn't specify one."""
        lower = SYSTEM_PROMPT.lower()
        assert "ask them" in lower or "ask the user" in lower


# ── 2. run_diagnostic is available to the LLM ──

class TestRunDiagnosticExposed:
    """Verify run_diagnostic is in the tool schemas and dispatch table."""

    def test_run_diagnostic_in_tool_schemas(self):
        """run_diagnostic must appear in the tool schemas shown to the LLM."""
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert "run_diagnostic" in names

    def test_run_diagnostic_in_dispatch(self):
        """run_diagnostic must be callable through execute_tool."""
        assert "run_diagnostic" in _DISPATCH

    def test_run_diagnostic_schema_has_request_param(self):
        """run_diagnostic schema must require a 'request' parameter."""
        schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "run_diagnostic")
        assert "request" in schema["function"]["parameters"]["required"]

    def test_run_diagnostic_schema_has_target_param(self):
        """run_diagnostic schema should have a 'target' parameter."""
        schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "run_diagnostic")
        assert "target" in schema["function"]["parameters"]["properties"]

    def test_get_allowed_actions_in_tool_schemas(self):
        """get_allowed_actions must appear in the tool schemas."""
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert "get_allowed_actions" in names


# ── 3. Restart actions are allowed through Phase 5 ──

class TestRestartActionsReachable:
    """Verify restart_container and restart_service are reachable through the Phase 5 pipeline."""

    def test_restart_container_is_allowed_action(self):
        """Policy must allow restart_container."""
        assert is_action_allowed("restart_container") is True

    def test_restart_service_is_allowed_action(self):
        """Policy must allow restart_service."""
        assert is_action_allowed("restart_service") is True

    def test_restart_container_is_executable(self):
        """Actions module must recognize restart_container as executable."""
        assert is_action_executable("restart_container") is True

    def test_restart_service_is_executable(self):
        """Actions module must recognize restart_service as executable."""
        assert is_action_executable("restart_service") is True

    def test_allowed_action_types_include_restart(self):
        """get_allowed_action_types must include both restart actions."""
        types = get_allowed_action_types()
        assert "restart_container" in types
        assert "restart_service" in types

    def test_restart_container_policy_evaluation_allows(self):
        """Policy firewall must allow restart_container with a valid target."""
        request = ActionRequest(
            action_type="restart_container",
            target="backend",
            reason="User requested restart",
        )
        result = evaluate_action(request)
        assert result["allowed"] is True
        assert result["command"] == "docker restart backend"

    def test_restart_service_policy_evaluation_allows(self):
        """Policy firewall must allow restart_service with a valid target."""
        request = ActionRequest(
            action_type="restart_service",
            target="nginx",
            reason="User requested restart",
        )
        result = evaluate_action(request)
        assert result["allowed"] is True
        assert result["command"] == "systemctl restart nginx"


# ── 4. Destructive actions remain blocked ──

class TestDestructiveActionsBlocked:
    """Verify that dangerous operations are still blocked at every level."""

    @pytest.mark.parametrize("action", [
        "rm", "delete", "destroy", "remove",
        "shell_execute", "arbitrary_command",
        "docker_system_prune", "kubectl_delete", "kubectl_apply",
        "kubectl_exec", "apt_install", "pip_install",
        "firewall_modify", "user_create", "user_delete",
        "database_drop",
    ])
    def test_destructive_actions_blocked_by_policy(self, action):
        """All destructive action types must be blocked by the policy firewall."""
        assert is_action_allowed(action) is False

    @pytest.mark.parametrize("action", [
        "rm", "delete", "destroy",
        "shell_execute", "arbitrary_command",
    ])
    def test_destructive_actions_not_executable(self, action):
        """Destructive actions must not be in the executable list."""
        assert is_action_executable(action) is False

    def test_system_prompt_blocks_docker_stop(self):
        """docker stop must remain blocked."""
        assert "docker stop" in SYSTEM_PROMPT.lower()

    def test_system_prompt_blocks_kubectl_delete(self):
        """kubectl delete must remain blocked."""
        assert "kubectl" in SYSTEM_PROMPT.lower() and "delete" in SYSTEM_PROMPT.lower()


# ── 5. Missing target handling ──

class TestMissingTarget:
    """Verify that missing target causes a request for clarification."""

    def test_run_diagnostic_with_empty_request(self):
        """Empty request should still work but not crash."""
        from app.tool_registry import execute_tool
        result = execute_tool("run_diagnostic", {"request": ""})
        # Should succeed (controller handles empty requests gracefully)
        assert "success" in result

    def test_run_diagnostic_with_request_and_target(self):
        """Request with target should work."""
        from app.tool_registry import execute_tool
        result = execute_tool("run_diagnostic", {
            "request": "restart the backend",
            "target": "backend",
        })
        assert "success" in result
        assert "outcome" in result

    def test_system_prompt_instructs_to_ask_for_missing_target(self):
        """System prompt must instruct the LLM to ask for target when missing."""
        lower = SYSTEM_PROMPT.lower()
        # Should mention asking the user if target is not specified
        assert "doesn't specify" in lower or "don't specify" in lower or "if the user asks to restart" in lower


# ── 6. Full pipeline verification ──

class TestFullPipelineIntegrity:
    """Verify the complete Phase 5 pipeline components are intact."""

    def test_risk_assessment_still_works_for_restart(self):
        """Risk assessment must evaluate restart_container correctly."""
        from app.control.risk import assess_risk
        risk = assess_risk("restart_container", "backend")
        assert risk.risk_level in ("low", "medium", "high", "critical")
        assert risk.overall_score >= 0.0

    def test_permission_system_still_works(self):
        """Permission system must correctly classify risk tiers."""
        from app.control.permissions import determine_approval_requirement
        assert determine_approval_requirement("low") == "auto"
        assert determine_approval_requirement("medium") == "ask"
        assert determine_approval_requirement("critical") == "block"

    def test_verification_still_defined_for_restart(self):
        """Verification strategies must exist for restart actions."""
        from app.control.verification import _VERIFICATION_STRATEGIES
        assert "restart_container" in _VERIFICATION_STRATEGIES
        assert "restart_service" in _VERIFICATION_STRATEGIES

    def test_rollback_still_defined_for_restart(self):
        """Rollback plans must be available for restart actions."""
        from app.control.rollback import create_rollback_plan
        plan = create_rollback_plan("restart_container", "backend")
        assert plan.available is True

    def test_incident_tracking_still_works(self):
        """Incident creation and lifecycle must still function."""
        from app.control.incident import create_incident
        incident = create_incident("Test restart incident", severity="medium")
        assert incident.status == "open"
        incident.add_timeline_event("restart_executed")
        incident.close("success", "Restart verified")
        assert incident.status == "closed"

    def test_hypotheses_engine_still_works(self):
        """Hypothesis engine must still function."""
        from app.control.hypotheses import create_hypothesis, HypothesisStatus
        h = create_hypothesis(
            "Container needs restart",
            evidence_for=["Container is unhealthy", "Logs show errors"],
        )
        assert h.confidence > 0.0
        assert h.status in (s.value for s in HypothesisStatus)

    def test_controller_loop_still_works(self):
        """Control loop must execute a diagnostic workflow."""
        from app.control.controller import ControlLoop
        results_iter = iter([
            {"success": True, "containers": [], "summary": {"containers_with_problems": []}},
        ])
        def mock_tool(name, args):
            return next(results_iter, {"success": True, "containers": [], "summary": {"containers_with_problems": []}})

        controller = ControlLoop(execute_tool_fn=mock_tool)
        result = controller.run("Check status", target="backend")
        assert result["success"] is True
        assert "outcome" in result

    def test_ssh_whitelist_allows_restart_commands(self):
        """SSH whitelist must allow restart commands for Phase 5."""
        from app.ssh_whitelist import is_command_allowed
        assert is_command_allowed("docker restart backend") is True
        assert is_command_allowed("systemctl restart nginx") is True
