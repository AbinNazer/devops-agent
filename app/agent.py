"""
The Agent owns the loop: send messages to the LLM, execute any tool calls
it asks for, feed results back, repeat until the LLM has a final answer.

This is the ONLY place tool functions actually get called. The LLM never
executes anything directly — it only ever returns "call this tool with
these arguments" as data, which the Agent decides whether/how to act on.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("agent")

SYSTEM_PROMPT = """You are a professional DevOps assistant investigating local, VPS, \
and AWS infrastructure.

Rules:
- Never invent infrastructure information. If you need real data, call a tool.
- Base every conclusion on actual tool results you received this conversation.
- Never claim you ran a tool if you didn't.
- You cannot execute arbitrary shell commands. You must NEVER construct or \
execute raw shell strings. All infrastructure interaction goes through tools.
- Some tools are read-only (get_*, docker_status, check_*, list_*, etc.).
- For safe controlled actions (restart a container, restart a service), use \
the `run_diagnostic` tool — it routes through a controlled pipeline with \
risk assessment, approval, verification, and rollback. Never attempt to \
restart anything directly. When the user asks to restart something, call \
`run_diagnostic` with mode="immediate" and let the control loop handle it. \
Do not inspect containers first unless the user explicitly asks for \
diagnostics, logs, health, or verification.
- If the user asks to restart something but doesn't specify a target \
  (container name or service name), ask them which container or service \
  before calling run_diagnostic — do not guess or pick one.
- If the user asks to restart multiple containers or services, ask one concise \
question before acting: "Run diagnostics first, or restart immediately?" \
If they choose diagnostics, call run_diagnostic with mode="diagnostic". If they choose \
immediately, call run_diagnostic with mode="immediate". Never silently choose \
between those modes.
- The following remain PERMANENTLY BLOCKED and must never be attempted: \
delete, destroy, rm, docker exec, docker stop, docker rm, kubectl \
delete/apply/exec, systemctl stop/disable, package installation, \
firewall modification, user management, database destructive operations, \
sudo, curl|bash, wget|bash, or any arbitrary shell command.
- If the tools available don't give you enough information to answer \
confidently, say so explicitly rather than guessing.

Phase 5 ground truth — only describe capabilities that are actually \
implemented. Never invent implementation details:
- Allowed actions: ONLY restart_container (docker restart) and \
restart_service (systemctl restart). Nothing else.
- Rollback for restart actions: the system restarts the container/service \
again. It does NOT stop/recreate containers, does NOT use docker run, \
cannot restore previous Docker configuration, and has no stored snapshots \
of container state.
- Verification: checks container running state, docker health status \
(via docker inspect), and recent logs for crash indicators (panic, \
fatal, segfault, killed, oom). For services: checks systemctl status. \
There is NO HTTP health probing, NO curl-based checks.
- Risk assessment: computed dynamically from 7 weighted factors (severity, \
blast radius, reversibility, production impact, destructive potential, \
confidence, dependency impact). Do NOT state a specific numeric score \
unless you are quoting an actual tool result.
- There are no stored historical success rates, no management keys, and \
no pre-captured container configurations.
- If you are explaining what WOULD happen during a hypothetical action, \
state only what the actual code does. If something is not implemented, \
say so. Do not present hypothetical capabilities as existing features.

Phase 6 monitoring ground truth:
- Monitoring tools (get_monitoring_status, get_active_incidents, \
get_incident_detail, get_monitoring_summary, explain_anomaly) are \
READ-ONLY. They never execute mutations.
- The monitoring engine detects anomalies using deterministic threshold \
checks, NOT LLM reasoning. Normal monitoring cycles do NOT call the LLM.
- When reporting monitoring data, quote actual tool results. Do NOT \
invent incident details, timestamps, severity levels, or evidence that \
was not returned by the tool.
- Monitoring does NOT directly restart, stop, delete, or modify \
anything. All controlled actions go through Phase 5.
- If asked about monitoring capabilities, describe only what the \
monitoring tools actually return. If something is not exposed, say so.
- Investigate before concluding — for a vague question ("is my server ok?", \
"why is X slow?"), gather the relevant metrics/logs before answering.
- For broad requests ("check everything", "is anything wrong", "how's my \
infrastructure"), call check_infrastructure_health ONCE rather than calling \
many individual tools — it already gathers local, VPS, and AWS status in a \
single pass. Only follow up with a specific tool if that summary points to \
something worth investigating further.
- Local machine tools (get_local_*) and VPS tools (get_cpu_usage, \
docker_status, etc.) look at DIFFERENT machines — never mix them up or \
present local results as if they were about the VPS or vice versa.
- If a status is UNKNOWN or NOT_CONFIGURED, say so plainly — never round \
that up to "healthy" or "fine."
- When using memory, prioritize current evidence and live check results over historical memory facts.
- Keep responses concise but useful.
"""

MAX_TOOL_ITERATIONS = 8  # safety valve against infinite tool-call loops

# These tools are read-only and safe to run concurrently. Control actions,
# terminal operations, and anything with approval semantics stay sequential.
PARALLEL_READ_TOOLS = frozenset({
    "docker_status", "docker_logs", "docker_inspect", "docker_stats",
    "docker_health_status", "get_cpu_usage", "get_memory_usage",
    "get_disk_usage", "get_uptime", "get_service_status",
    "check_network_connectivity", "get_k3s_nodes", "get_k3s_pods",
    "get_k3s_deployments", "get_k3s_services", "get_k3s_events",
    "get_k3s_cluster_status", "get_monitoring_status", "get_active_incidents",
    "get_monitoring_summary", "explain_anomaly", "analyze_project",
    "analyze_runtime_sources", "list_project_tree",
})


class Agent:
    def __init__(self, provider, tool_schemas: list, execute_tool_fn,
                 memory_command_handler=None, allowed_tool_names=None):
        """
        provider: an LLMProvider instance
        tool_schemas: list of tool schemas to show the LLM
        execute_tool_fn: callable(name, arguments) -> dict result
        allowed_tool_names: optional frozenset/set of tool names the LLM
            may request.  If provided, any tool call whose name is NOT in
            this set is rejected before execution and a clear error is
            returned to the LLM.  This prevents hallucinated tool names
            (e.g. "container_stats" when only "docker_stats" is registered)
            from reaching the dispatch layer.
        """
        self.provider = provider
        self.tool_schemas = tool_schemas
        self.execute_tool_fn = execute_tool_fn
        self.memory_command_handler = memory_command_handler
        self.allowed_tool_names = allowed_tool_names

    def run(self, user_input: str, history: list, on_tool_call=None,
            on_tool_result=None) -> str:
        """
        Run one full turn: append the user's message, loop through however
        many tool calls the LLM needs, and return the final text answer.
        `history` is mutated in place so the caller keeps the conversation.

        on_tool_call:   optional callback(name, arguments) invoked right
                        before each tool executes.
        on_tool_result: optional callback(name, result) invoked right
                        after each tool finishes — used by the SSE bridge
                        to stream live feedback.
        """
        if self.memory_command_handler:
            direct_answer = self.memory_command_handler(user_input)
            if direct_answer is not None:
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": direct_answer})
                return direct_answer
        history.append({"role": "user", "content": user_input})
        logger.info("user_request=%r", user_input)

        content = ""
        called_tools = set()

        for iteration in range(MAX_TOOL_ITERATIONS):
            result = self.provider.chat(history, self.tool_schemas)
            content = result["content"]
            tool_calls = result["tool_calls"]

            history.append(self.provider.assistant_message(content, tool_calls))

            if not tool_calls:
                return content

            runnable = []
            for tc in tool_calls:
                tc_sig = (tc["name"], str(tc.get("arguments", {})))
                if tc_sig in called_tools:
                    logger.warning("tool_duplicate_skipped=%s", tc["name"])
                    history.append(self.provider.tool_result_message(
                        tc, {"success": False, "error": "Duplicate tool call detected and skipped to prevent looping."}
                    ))
                    continue
                called_tools.add(tc_sig)

                # Validate the tool name against the registered set.
                # This catches hallucinated names (e.g. "container_stats" when
                # only "docker_stats" is registered) before they reach dispatch.
                if (self.allowed_tool_names is not None
                        and tc["name"] not in self.allowed_tool_names):
                    logger.warning("tool_not_registered=%s", tc["name"])
                    tool_result = {
                        "success": False,
                        "error": (
                            f"'{tc['name']}' is not a registered tool. "
                            f"Available tools: {sorted(self.allowed_tool_names)}"
                        ),
                    }
                    history.append(self.provider.tool_result_message(tc, tool_result))
                    continue

                runnable.append(tc)

            def execute_one(tc):
                if on_tool_call:
                    on_tool_call(tc["name"], tc["arguments"])
                logger.info("tool_selected=%s arguments=%s", tc["name"], tc["arguments"])
                return self.execute_tool_fn(tc["name"], tc["arguments"])

            # An immediate restart is an explicit mutation request. Once the
            # model has supplied the resolved target, do not let it add a
            # second round of status/health calls or repeat the action. This
            # keeps the operation fast and prevents tool-loop exhaustion.
            immediate_restart = next(
                (
                    tc for tc in runnable
                    if tc["name"] == "run_diagnostic"
                    and tc.get("arguments", {}).get("mode") == "immediate"
                    and any(word in tc.get("arguments", {}).get("request", "").lower().split()
                            for word in ("restart", "reboot"))
                ),
                None,
            )
            if immediate_restart is not None:
                runnable = [immediate_restart]

            parallel = len(runnable) > 1 and all(tc["name"] in PARALLEL_READ_TOOLS for tc in runnable)
            if parallel:
                with ThreadPoolExecutor(max_workers=min(4, len(runnable))) as pool:
                    results = list(pool.map(execute_one, runnable))
            else:
                results = [execute_one(tc) for tc in runnable]

            for tc, tool_result in zip(runnable, results):
                if tool_result.get("success") is False:
                    logger.warning("tool_failed=%s error=%s", tc["name"], tool_result.get("error"))
                else:
                    logger.info("tool_succeeded=%s", tc["name"])
                if on_tool_result:
                    on_tool_result(tc["name"], tool_result)
                history.append(self.provider.tool_result_message(tc, tool_result))

            if immediate_restart is not None:
                result = results[0] if results else {}
                actions = result.get("actions_taken") or []
                if actions:
                    completed = sum(1 for action in actions if action.get("result", {}).get("success"))
                    return f"Restart operation completed: {completed}/{len(actions)} action(s) succeeded."
                return result.get("summary") or result.get("outcome") or "Restart operation did not execute."

        # Safety valve hit — return whatever text we have, or a clear admission
        logger.warning("max_tool_iterations_reached")
        return content or "I wasn't able to reach a conclusion within a reasonable number of tool calls."
