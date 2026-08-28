"""
The Agent owns the loop: send messages to the LLM, execute any tool calls
it asks for, feed results back, repeat until the LLM has a final answer.

This is the ONLY place tool functions actually get called. The LLM never
executes anything directly — it only ever returns "call this tool with
these arguments" as data, which the Agent decides whether/how to act on.
"""
import logging

logger = logging.getLogger("agent")

SYSTEM_PROMPT = """You are a professional DevOps assistant investigating local, VPS, \
and AWS infrastructure.

Rules:
- Never invent infrastructure information. If you need real data, call a tool.
- Base every conclusion on actual tool results you received this conversation.
- Never claim you ran a tool if you didn't.
- You cannot execute shell commands or perform destructive operations — you \
only have the read-only tools you've been given. If asked to do something \
destructive (delete, restart, deploy, modify), say plainly that this isn't \
available yet.
- If the tools available don't give you enough information to answer \
confidently, say so explicitly rather than guessing.
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


class Agent:
    def __init__(self, provider, tool_schemas: list, execute_tool_fn):
        """
        provider: an LLMProvider instance
        tool_schemas: list of tool schemas to show the LLM
        execute_tool_fn: callable(name, arguments) -> dict result
        """
        self.provider = provider
        self.tool_schemas = tool_schemas
        self.execute_tool_fn = execute_tool_fn

    def run(self, user_input: str, history: list, on_tool_call=None) -> str:
        """
        Run one full turn: append the user's message, loop through however
        many tool calls the LLM needs, and return the final text answer.
        `history` is mutated in place so the caller keeps the conversation.

        on_tool_call: optional callback(name, arguments) invoked right
        before each tool executes, so a caller (like the CLI) can display
        progress without the Agent needing to know about print/rich/etc.
        """
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

            for tc in tool_calls:
                tc_sig = (tc["name"], str(tc.get("arguments", {})))
                if tc_sig in called_tools:
                    logger.warning("tool_duplicate_skipped=%s", tc["name"])
                    history.append(self.provider.tool_result_message(
                        tc, {"success": False, "error": "Duplicate tool call detected and skipped to prevent looping."}
                    ))
                    continue
                called_tools.add(tc_sig)

                if on_tool_call:
                    on_tool_call(tc["name"], tc["arguments"])
                logger.info("tool_selected=%s arguments=%s", tc["name"], tc["arguments"])
                tool_result = self.execute_tool_fn(tc["name"], tc["arguments"])
                if tool_result.get("success") is False:
                    logger.warning("tool_failed=%s error=%s", tc["name"], tool_result.get("error"))
                else:
                    logger.info("tool_succeeded=%s", tc["name"])
                history.append(self.provider.tool_result_message(tc, tool_result))

        # Safety valve hit — return whatever text we have, or a clear admission
        logger.warning("max_tool_iterations_reached")
        return content or "I wasn't able to reach a conclusion within a reasonable number of tool calls."