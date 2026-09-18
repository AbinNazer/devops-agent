"""
Main Controller for the Phase 5 Control Loop.

Orchestrates the full lifecycle:
    observe → build_state → retrieve_memory → generate_hypotheses →
    create_plan → risk_assessment → approval → execute → verify →
    rollback_if_needed → record_outcome

Every loop has explicit bounds. The agent stops intelligently when
sufficient evidence has been collected.

This is the ONLY place the control loop runs. The Agent class can
delegate to this controller for structured diagnostic/action workflows.
"""
import logging
import re
import time
from typing import Callable, Dict, List, Optional

from app.control.state import (
    ActionRecord, ControlState, OutcomeStatus, Phase,
    ApprovalStatus, RiskLevel,
)
from app.control.planner import (
    plan_diagnostic_steps, should_stop_evidence_collection, can_take_action,
    MAX_CONTROL_STEPS, MAX_DIAGNOSTIC_COMMANDS, MAX_ACTIONS_PER_RUN,
)
from app.control.hypotheses import (
    create_hypothesis, select_best, add_evidence_for, add_evidence_against,
    rank_hypotheses, Hypothesis,
)
from app.control.risk import assess_risk
from app.control.permissions import (
    ApprovalRequest, evaluate_approval, simulate_user_approval,
    determine_approval_requirement,
)
from app.control.policy import (
    evaluate_action, ActionRequest, is_action_allowed, build_safe_command,
)
from app.control.actions import (
    execute_action, create_action_record, is_action_executable,
    build_action_command,
)
from app.control.verification import verify_action
from app.control.rollback import create_rollback_plan, execute_rollback, is_rollback_safe
from app.control.incident import create_incident, Incident

logger = logging.getLogger("control_controller")


class ControlLoop:
    """
    The main control loop for Phase 5.

    Orchestrates evidence collection, hypothesis generation, action planning,
    risk assessment, approval, execution, verification, and rollback.

    Usage:
        controller = ControlLoop(
            execute_tool_fn=execute_tool,
            user_approve_fn=lambda prompt: True,
        )
        result = controller.run("Why is the backend down?")
    """

    def __init__(
        self,
        execute_tool_fn: Callable[[str, Dict], Dict],
        user_approve_fn: Optional[Callable[[str], bool]] = None,
        memory_repository=None,
        ssh_session=None,
    ):
        """
        Args:
            execute_tool_fn: Function to execute a tool by name (from tool_registry).
            user_approve_fn: Function that takes an approval prompt and returns True/False.
            memory_repository: Optional MemoryRepository for Phase 4 integration.
            ssh_session: Optional SSHSession for scoped connection reuse.
        """
        self.execute_tool_fn = execute_tool_fn
        self.user_approve_fn = user_approve_fn or (lambda prompt: False)
        self.memory_repository = memory_repository
        self.ssh_session = ssh_session

    def run(self, request: str, environment: str = "", target: str = "", mode: str = "diagnostic") -> Dict:
        """
        Execute the full control loop for a user request.

        Returns a dict with the final outcome, incident record, and state summary.
        """
        state = ControlState(request=request, environment=environment, target=target)
        incident = create_incident(
            title=request,
            severity="unknown",
            environment=environment,
        )

        try:
            # Phase 1: OBSERVE
            state.transition_to(Phase.OBSERVE, "Starting observation")
            incident.add_timeline_event("observation_started", phase=Phase.OBSERVE.value)

            # Phase 2: BUILD STATE
            state.transition_to(Phase.BUILD_STATE, "Building diagnostic state")
            incident.add_timeline_event("state_built", phase=Phase.BUILD_STATE.value)

            # Phase 3: RETRIEVE MEMORY
            state.transition_to(Phase.RETRIEVE_MEMORY, "Checking historical memory")
            if self.memory_repository:
                self._retrieve_memory(state)
            incident.add_timeline_event("memory_retrieved",
                detail=f"Found {len(state.memory_references)} relevant memories",
                phase=Phase.RETRIEVE_MEMORY.value)

            # Phase 4: EVIDENCE COLLECTION (loop)
            state.transition_to(Phase.OBSERVE, "Collecting evidence")
            if mode == "immediate":
                incident.add_timeline_event("diagnostics_skipped", detail="Immediate mode selected")
            else:
                self._collect_evidence(state, incident)

            # Phase 5: GENERATE HYPOTHESES
            state.transition_to(Phase.GENERATE_HYPOTHESES, "Generating hypotheses")
            self._generate_hypotheses(state, incident)

            # Phase 6: CREATE PLAN
            state.transition_to(Phase.CREATE_PLAN, "Creating action plan")
            self._create_plan(state, incident)

            # Phase 7: RISK ASSESSMENT + APPROVAL + EXECUTION
            if state.action_plan and can_take_action(state):
                state.transition_to(Phase.RISK_ASSESSMENT, "Assessing action risk")
                action_result = self._execute_action_flow(state, incident)
                if action_result:
                    state.action_results.append(action_result)
            else:
                state.transition_to(Phase.RECORD_OUTCOME, "No action needed")
                state.set_outcome(
                    OutcomeStatus.SUCCESS if state.confidence >= 0.5 else OutcomeStatus.UNKNOWN,
                    self._build_diagnostic_summary(state),
                )

            # Phase 8: RECORD OUTCOME
            state.transition_to(Phase.RECORD_OUTCOME, "Recording outcome")
            self._record_outcome(state, incident)

            # Store incident in memory if available
            if self.memory_repository:
                self._store_incident_memory(state, incident)

            state.transition_to(Phase.COMPLETED, "Control loop completed")
            incident.close(state.final_outcome, state.outcome_summary)

            return {
                "success": True,
                "incident_id": state.incident_id,
                "outcome": state.final_outcome,
                "summary": state.outcome_summary,
                "hypotheses": state.hypotheses,
                "selected_hypothesis": state.selected_hypothesis,
                "confidence": state.confidence,
                "actions_taken": [a.to_dict() for a in state.actions_attempted],
                "verification": state.verification_results,
                "state": state.to_dict(),
                "incident": incident.to_dict(),
            }

        except Exception as e:
            logger.error("control_loop_error: %s", e)
            state.transition_to(Phase.FAILED, str(e))
            state.set_outcome(OutcomeStatus.FAILED, f"Control loop failed: {e}")
            incident.close("failed", str(e))
            return {
                "success": False,
                "error": str(e),
                "state": state.to_dict(),
                "incident": incident.to_dict(),
            }

    def _collect_evidence(self, state: ControlState, incident: Incident) -> None:
        """Collect evidence through tool calls using the planner."""
        plan = plan_diagnostic_steps(state)

        for step in plan.steps:
            if should_stop_evidence_collection(state):
                logger.info("evidence_collection_stopped: sufficient evidence")
                break

            # Execute the diagnostic tool
            result = self.execute_tool_fn(step.tool, step.arguments)
            state.add_observation(step.tool, result)
            state.diagnostic_command_count += 1

            if result.get("success"):
                incident.add_timeline_event(
                    f"tool_executed:{step.tool}",
                    detail=f"Success: {step.reason}",
                    phase=Phase.OBSERVE.value,
                )
                state.evidence.append(
                    f"Tool '{step.tool}' returned successfully: {step.reason}"
                )
            else:
                incident.add_timeline_event(
                    f"tool_failed:{step.tool}",
                    detail=f"Failed: {result.get('error', 'unknown')}",
                    phase=Phase.OBSERVE.value,
                )
                state.evidence.append(
                    f"Tool '{step.tool}' failed: {result.get('error', 'unknown')}"
                )

    def _generate_hypotheses(self, state: ControlState, incident: Incident) -> None:
        """Generate hypotheses based on collected evidence."""
        # Generate hypotheses from observations
        hypotheses = self._infer_hypotheses_from_evidence(state)

        for h in hypotheses:
            state.add_hypothesis(h.to_dict())
            incident.add_timeline_event(
                "hypothesis_generated",
                detail=f"Statement: {h.statement} (confidence: {h.confidence:.0%})",
                phase=Phase.GENERATE_HYPOTHESES.value,
            )

        # Select the best hypothesis
        best = select_best(hypotheses)
        if best:
            state.select_hypothesis(best.to_dict())
            incident.add_timeline_event(
                "hypothesis_selected",
                detail=f"Selected: {best.statement} (confidence: {best.confidence:.0%})",
                phase=Phase.GENERATE_HYPOTHESES.value,
            )

    def _infer_hypotheses_from_evidence(self, state: ControlState) -> List[Hypothesis]:
        """Infer hypotheses from collected evidence."""
        hypotheses = []

        for obs in state.observations:
            result = obs.result
            tool = obs.tool

            if not result.get("success"):
                continue

            # Docker-related hypotheses
            if tool == "docker_health_status":
                problems = result.get("summary", {}).get("containers_with_problems", [])
                if problems:
                    for container in problems:
                        h = create_hypothesis(
                            statement=f"Container '{container}' is having issues",
                            evidence_for=[f"Container '{container}' appears in problems list"],
                            related_components=[container],
                        )
                        hypotheses.append(h)

            elif tool == "docker_logs":
                logs = result.get("logs", "")
                container = result.get("container", "")
                error_indicators = ["error", "fatal", "panic", "exception", "timeout"]
                found = [ind for ind in error_indicators if ind in logs.lower()]
                if found:
                    h = create_hypothesis(
                        statement=f"Container '{container}' has errors in logs",
                        evidence_for=[f"Log contains: {', '.join(found)}"],
                        related_components=[container],
                    )
                    hypotheses.append(h)

            # Server-related hypotheses
            elif tool == "get_cpu_usage":
                load = result.get("load_per_core_1min", 0)
                if load > 2.0:
                    h = create_hypothesis(
                        statement="Server is under high CPU load",
                        evidence_for=[f"Load per core: {load}"],
                        related_components=["server"],
                    )
                    hypotheses.append(h)

            elif tool == "get_memory_usage":
                pct = result.get("memory_percent", 0)
                if pct > 85:
                    h = create_hypothesis(
                        statement="Server is running low on memory",
                        evidence_for=[f"Memory usage: {pct}%"],
                        related_components=["server"],
                    )
                    hypotheses.append(h)

            elif tool == "get_disk_usage":
                pct = result.get("disk_percent", 0)
                if pct > 85:
                    h = create_hypothesis(
                        statement="Server disk usage is critically high",
                        evidence_for=[f"Disk usage: {pct}%"],
                        related_components=["server"],
                    )
                    hypotheses.append(h)

            # Network-related hypotheses
            elif tool == "check_network_connectivity":
                if not result.get("reachable"):
                    h = create_hypothesis(
                        statement="VPS is unreachable",
                        evidence_for=["Network check shows VPS unreachable"],
                        related_components=["network"],
                    )
                    hypotheses.append(h)

        return hypotheses

    def _create_plan(self, state: ControlState, incident: Incident) -> None:
        """Create an action plan based on hypotheses."""
        # A direct, explicit restart request should not require a diagnostic
        # hypothesis first. It still goes through the complete safety pipeline
        # below: risk assessment, approval, execution, and verification.
        request_lower = state.request.lower()
        restart_requested = bool(re.search(r"\b(restart|reboot)\b", request_lower))
        direct_target = (state.target or "").strip()
        if not direct_target and restart_requested:
            match = re.search(
                r"\b(?:container|service)\s+([a-zA-Z0-9][a-zA-Z0-9_.-]*)\b",
                state.request,
                re.IGNORECASE,
            )
            if match:
                direct_target = match.group(1)

        if restart_requested and direct_target:
            action_type = "restart_service" if re.search(r"\bservice\b", request_lower) else "restart_container"
            targets = [item.strip() for item in re.split(r",|\band\b", direct_target, flags=re.IGNORECASE) if item.strip()]
            state.action_plan = [{
                "action_type": action_type,
                "target": item,
                "reason": f"User explicitly requested restarting '{item}'.",
            } for item in targets]
            incident.add_timeline_event(
                "direct_action_plan_created",
                detail=f"Explicit {action_type} request for {len(targets)} target(s)",
                phase=Phase.CREATE_PLAN.value,
            )
            return

        if not state.selected_hypothesis:
            return

        hypothesis = state.selected_hypothesis
        components = hypothesis.get("related_components", [])

        plan = []
        for component in components:
            # Only plan restart actions if the component is a known container/service
            if component and is_action_executable("restart_container"):
                plan.append({
                    "action_type": "restart_container",
                    "target": component,
                    "reason": f"Restart '{component}' to resolve: {hypothesis.get('statement', '')}",
                })

        state.action_plan = plan

        if plan:
            incident.add_timeline_event(
                "action_plan_created",
                detail=f"Plan has {len(plan)} action(s)",
                phase=Phase.CREATE_PLAN.value,
            )

    def _execute_action_flow(self, state: ControlState, incident: Incident) -> Optional[Dict]:
        """Execute the full action flow: risk → approval → execute → verify → rollback."""
        for action in state.action_plan:
            if state.action_count >= MAX_ACTIONS_PER_RUN:
                break

            action_type = action.get("action_type", "")
            target = action.get("target", "")

            # Risk assessment
            risk = assess_risk(
                action_type, target,
                context={"confidence": state.confidence},
            )
            state.risk_level = risk.risk_level
            incident.add_timeline_event(
                "risk_assessed",
                detail=f"Risk: {risk.risk_level} (score: {risk.overall_score})",
                phase=Phase.RISK_ASSESSMENT.value,
            )

            # Approval
            state.transition_to(Phase.APPROVAL, f"Requesting approval for {action_type}")
            approval_request = ApprovalRequest(
                action_type=action_type,
                target=target,
                command=build_action_command(action_type, target) or "",
                risk_level=risk.risk_level,
                reason=action.get("reason", ""),
                evidence="\n".join(state.evidence[:5]),
                expected_impact=f"Service interruption: ~5-20 seconds",
                rollback_plan="Restart again if needed" if risk.rollback_available else "No rollback",
                reversible=risk.reversible,
            )
            approval = evaluate_approval(approval_request)
            state.approval_status = approval["status"]

            if approval["status"] == ApprovalStatus.BLOCKED.value:
                incident.add_timeline_event(
                    "action_blocked",
                    detail=f"Action blocked: {approval.get('reason', '')}",
                    phase=Phase.APPROVAL.value,
                )
                state.set_outcome(OutcomeStatus.BLOCKED, approval.get("reason", ""))
                return None

            if approval["status"] == ApprovalStatus.PENDING.value:
                # Ask the user
                prompt = approval.get("prompt", "Proceed? [y/N]")
                approved = self.user_approve_fn(prompt)
                user_decision = simulate_user_approval(approved)
                state.approval_status = user_decision["status"]
                state.approval_details = user_decision

                incident.add_timeline_event(
                    "user_responded",
                    detail=f"Approved: {approved}",
                    phase=Phase.APPROVAL.value,
                )

                if not approved:
                    state.set_outcome(OutcomeStatus.BLOCKED, "User denied the action.")
                    return None

            # Execute
            state.transition_to(Phase.EXECUTE, f"Executing {action_type}")
            record = create_action_record(
                action_type, target, approval_request.command,
                risk_level=risk.risk_level,
                approval_status=state.approval_status,
            )
            state.add_action_record(record)

            exec_result = execute_action(action_type, target)
            state.action_count += 1
            record.result = exec_result

            incident.add_timeline_event(
                "action_executed",
                detail=f"Result: {'success' if exec_result.get('success') else 'failed'}",
                phase=Phase.EXECUTE.value,
            )

            if not exec_result.get("success"):
                state.set_outcome(
                    OutcomeStatus.FAILED,
                    f"Action failed: {exec_result.get('error', 'unknown')}",
                )
                return exec_result

            # Verify
            state.transition_to(Phase.VERIFY, f"Verifying {action_type}")
            verification = verify_action(action_type, target)
            record.verification = verification.to_dict()
            state.add_verification(verification.to_dict())

            incident.add_timeline_event(
                "verification_complete",
                detail=f"Status: {verification.status}",
                phase=Phase.VERIFY.value,
            )

            if verification.status == "success":
                state.set_outcome(
                    OutcomeStatus.SUCCESS,
                    f"Action '{action_type}' on '{target}' verified successfully.",
                )
                return exec_result

            # Rollback if verification failed
            if verification.status in ("failed", "degraded"):
                state.transition_to(Phase.ROLLBACK, f"Considering rollback for {action_type}")
                rollback_plan = create_rollback_plan(action_type, target)

                if rollback_plan.available:
                    incident.add_timeline_event(
                        "rollback_plan_created",
                        detail=f"Rollback available: {rollback_plan.reason}",
                        phase=Phase.ROLLBACK.value,
                    )

                    if is_rollback_safe(rollback_plan):
                        rollback_result = execute_rollback(rollback_plan)
                        record.rollback = rollback_result.to_dict()
                        state.rollback_results.append(rollback_result.to_dict())

                        incident.add_timeline_event(
                            "rollback_executed",
                            detail=f"Rollback {'succeeded' if rollback_result.success else 'failed'}",
                            phase=Phase.ROLLBACK.value,
                        )

                        # Verify rollback
                        if rollback_result.success:
                            rollback_verification = verify_action(action_type, target)
                            if rollback_verification.status == "success":
                                state.set_outcome(
                                    OutcomeStatus.ROLLED_BACK,
                                    f"Action rolled back successfully after verification failure.",
                                )
                            else:
                                state.set_outcome(
                                    OutcomeStatus.FAILED,
                                    f"Rollback verification also failed.",
                                )
                        else:
                            state.set_outcome(
                                OutcomeStatus.FAILED,
                                f"Rollback failed: {rollback_result.error}",
                            )
                    else:
                        state.set_outcome(
                            OutcomeStatus.FAILED,
                            f"Rollback requires manual intervention.",
                        )
                else:
                    state.set_outcome(
                        OutcomeStatus.FAILED,
                        f"Verification failed and no rollback available.",
                    )

                return exec_result

            # Default: unknown verification status
            state.set_outcome(OutcomeStatus.UNKNOWN, f"Verification status: {verification.status}")
            return exec_result

        return None

    def _retrieve_memory(self, state: ControlState) -> None:
        """Retrieve relevant memories for the request."""
        if not self.memory_repository:
            return

        try:
            memories = self.memory_repository.search(
                query=state.request, limit=5,
            )
            for memory in memories:
                state.memory_references.append(memory.id)
                # Add memory context as evidence
                state.evidence.append(
                    f"[Memory] {memory.title}: {memory.content[:200]}"
                )
        except Exception as e:
            logger.warning("memory_retrieval_error: %s", e)

    def _record_outcome(self, state: ControlState, incident: Incident) -> None:
        """Record the final outcome in the incident."""
        incident.severity = self._determine_severity(state)
        incident.outcome = state.final_outcome
        incident.outcome_summary = state.outcome_summary
        incident.confidence = state.confidence
        incident.selected_hypothesis = state.selected_hypothesis
        incident.hypotheses = state.hypotheses
        incident.evidence = state.evidence
        incident.observations = [o.to_dict() for o in state.observations]
        incident.actions = [a.to_dict() for a in state.actions_attempted]
        incident.verification_results = state.verification_results

    def _store_incident_memory(self, state: ControlState, incident: Incident) -> None:
        """Store the incident in memory for future reference."""
        if not self.memory_repository:
            return

        try:
            from app.memory.models import Memory
            memory = Memory(
                type="episodic",
                title=f"Incident: {state.request[:100]}",
                content=incident.to_memory_content(),
                environment=state.environment,
                component=state.target,
                tags=["incident", state.final_outcome],
                confidence="High" if state.confidence >= 0.7 else "Medium",
                importance=3.0,
                source="control_loop",
                outcome=state.final_outcome,
            )
            stored = self.memory_repository.store_memory(memory)
            state.memory_stored.append(stored.id)
            incident.memory_id = stored.id
        except Exception as e:
            logger.warning("memory_store_error: %s", e)

    def _determine_severity(self, state: ControlState) -> str:
        """Determine incident severity from outcome and risk."""
        if state.final_outcome == OutcomeStatus.FAILED.value:
            return "high"
        if state.final_outcome == OutcomeStatus.ROLLED_BACK.value:
            return "medium"
        if state.risk_level == RiskLevel.CRITICAL.value:
            return "critical"
        if state.risk_level == RiskLevel.HIGH.value:
            return "high"
        return "low"

    def _build_diagnostic_summary(self, state: ControlState) -> str:
        """Build a human-readable summary of the diagnostic findings."""
        lines = []

        if state.selected_hypothesis:
            lines.append(
                f"Primary hypothesis: {state.selected_hypothesis.get('statement', 'N/A')}"
            )
            lines.append(f"Confidence: {state.confidence:.0%}")
        elif state.hypotheses:
            lines.append(f"Generated {len(state.hypotheses)} hypotheses but none selected with sufficient confidence.")
        else:
            lines.append("No hypotheses generated from collected evidence.")

        lines.append(f"Observations: {len(state.observations)}")
        lines.append(f"Tools used: {len(state.executed_tools)}")

        if state.action_plan:
            lines.append(f"Planned actions: {len(state.action_plan)}")
        else:
            lines.append("No remediation actions planned.")

        return "\n".join(lines)
