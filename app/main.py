#!/usr/bin/env python3
"""
CLI entry point. Wires together config -> provider -> agent -> tools,
and runs a chat loop with a few slash commands for session management.

Usage:
    python -m app.main

Slash commands:
    /help            show available commands
    /reset           clear the current conversation (keeps the system prompt)
    /save [name]     save the current conversation to sessions/
    /load <name>     load a previously saved conversation
    /history         list saved sessions
    /health          check VPS reachability directly, without going through the LLM
    /quit            exit (same as Ctrl+C)
"""
import logging
import sys

from app.config import Config, validate_vps_config
from app.llm_provider import build_provider
from app.llm_router import build_router
from app.tool_registry import TOOL_SCHEMAS, TOOL_NAMES, execute_tool, handle_memory_command, set_control_approval_callback
from app.agent import Agent, SYSTEM_PROMPT
from app.session import save_session, load_session, list_sessions
from app.logging_config import setup_logging
from app.ssh_client import close_connection
from app.tools.network import check_network_connectivity

try:
    from rich.console import Console
    _console = Console()
    _RICH = True
except ImportError:
    _console = None
    _RICH = False

setup_logging()
logger = logging.getLogger("main")

HELP_TEXT = """Commands:
  /help            show this message
  /reset           clear the current conversation
  /save [name]     save conversation to sessions/ (default: timestamp)
  /load <name>     load a saved conversation by name
  /history         list saved sessions
  /health          check VPS reachability directly (bypasses the LLM)
  /quit            exit
Anything else is sent to the agent as a question."""


def _print_assistant(text: str):
    if _RICH:
        _console.print(f"[bold cyan]assistant>[/bold cyan] {text}\n")
    else:
        print(f"\nassistant> {text}\n")


def _print_system(text: str):
    if _RICH:
        _console.print(f"[dim]{text}[/dim]")
    else:
        print(text)


def _print_tool_call(name: str, args: dict):
    if _RICH:
        _console.print(f"  [yellow]→ {name}[/yellow]({args})")
    else:
        print(f"  [running {name}({args})]")


def fresh_history() -> list:
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def _run_health_check():
    """Direct VPS reachability check — bypasses the LLM entirely, useful
    for a quick manual sanity check without spending a model call on it."""
    result = check_network_connectivity()
    if not result.get("reachable"):
        _print_system(f"VPS unreachable: {result.get('detail', 'unknown error')}")
    else:
        _print_system(f"VPS reachable — {result['latency_ms']}ms round trip.")


def _approve_control_action(prompt: str) -> bool:
    """Ask for explicit approval before a restart action in the CLI."""
    _print_system("\n" + prompt)
    try:
        answer = input("Approve this action? [y/N] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False
    return answer in {"y", "yes"}


def main():
    # Fail loudly and early on VPS config problems, rather than only
    # discovering them on the first tool call mid-conversation.
    vps_problems = validate_vps_config()
    if vps_problems:
        _print_system("VPS configuration warning — VPS-dependent tools will fail until fixed:")
        for p in vps_problems:
            _print_system(f"  - {p}")
        _print_system("")  # blank line; chat still works for non-VPS questions

    try:
        provider = build_router(Config)
        provider.check_alive()
    except Exception as e:
        print(f"Couldn't start LLM provider: {e}")
        sys.exit(1)

    set_control_approval_callback(_approve_control_action)
    agent = Agent(provider=provider, tool_schemas=TOOL_SCHEMAS, execute_tool_fn=execute_tool, memory_command_handler=handle_memory_command, allowed_tool_names=TOOL_NAMES)
    history = fresh_history()

    _print_system(f"DevOps Agent ready — {provider.name}. Type /help for commands, Ctrl+C to quit.\n")

    try:
        while True:
            try:
                user_input = input("you> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nbye")
                break
            if not user_input:
                continue

            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else None

                if cmd == "/help":
                    _print_system(HELP_TEXT)
                elif cmd == "/reset":
                    history = fresh_history()
                    _print_system("Conversation cleared.")
                elif cmd == "/save":
                    path = save_session(history, arg)
                    _print_system(f"Saved to {path}")
                elif cmd == "/load":
                    if not arg:
                        _print_system("Usage: /load <name>")
                    else:
                        try:
                            history = load_session(arg)
                            _print_system(f"Loaded '{arg}' ({len(history)} messages).")
                        except FileNotFoundError:
                            _print_system(f"No saved session named '{arg}'. Try /history to see available ones.")
                elif cmd == "/history":
                    sessions = list_sessions()
                    _print_system("Saved sessions:\n  " + "\n  ".join(sessions) if sessions else "No saved sessions yet.")
                elif cmd == "/health":
                    _run_health_check()
                elif cmd in ("/quit", "/exit"):
                    print("bye")
                    break
                else:
                    _print_system(f"Unknown command '{cmd}'. Type /help for the list.")
                continue

            try:
                def on_tool_call(name, args):
                    _print_tool_call(name, args)

                answer = agent.run(user_input, history, on_tool_call=on_tool_call)
                _print_assistant(answer)
            except Exception as e:
                logger.error("agent_error=%s", e)
                print(f"\nSomething went wrong: {e}\n")
                history.pop()  # drop the failed user turn so state stays clean
    finally:
        close_connection()  # clean shutdown of any reused SSH connection


if __name__ == "__main__":
    main()
