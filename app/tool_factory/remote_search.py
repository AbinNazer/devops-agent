"""Secure remote search over the unified executor.

Operations are rooted at explicit allowed roots from server-side
configuration (JARVIS_SEARCH_ALLOWED_ROOTS). The browser/LLM can propose a
root-relative path and a search term — never an arbitrary path, never an
arbitrary command. Every resolved command is enforced by ssh_whitelist
(patterns registered from server config at startup) and executed through
app.executor.run_command in both local and SSH modes.
"""
from __future__ import annotations

import logging
import re

from app.config import Config
from app.ssh_whitelist import register_readonly_pattern
from app.tool_factory.redaction import redact_line

logger = logging.getLogger("remote_search")

MAX_MATCHES = 50
MAX_FILE_BYTES = 2_000_000
MAX_TIMEOUT_FLAG = 30          # grep/tail -m style bounds are baked into commands
MAX_LINE_LENGTH = 500

# Files whose contents must never be returned, regardless of root.
FORBIDDEN_FILE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
FORBIDDEN_FILE_SUFFIXES = {".pem", ".key", ".p12", ".db", ".sqlite"}

_SAFE_TERM_RE = re.compile(r"^[\w.\-@/:+ ]{1,120}$")
_SAFE_RELATIVE_RE = re.compile(r"^[\w.\-/]{0,200}$")
_ABSOLUTE_ROOT_RE = re.compile(r"^/[\w./\-]*$")

# Commands registered per root at startup. The literal root is baked in;
# {path} and {query} are strict whitelist slots (see ssh_whitelist).
# tail/maxdepth variants are registered per bounded line-count/depth bucket
# so per-request integers are never free-form.
_SEARCH_TEMPLATE = "grep -rIn -m 50 -e {query} {root}{path}"
_LIST_TEMPLATE = "find {root}{path} -maxdepth {depth} -type f"
_TAIL_TEMPLATE = "tail -n {lines} {root}{path}"

# Bounded buckets for numeric parameters.
_TAIL_BUCKETS = (10, 50, 100, 200, 500)
_DEPTH_BUCKETS = (1, 2, 3, 4)

_registered_roots: list[str] = []
_registration_done = False


def get_allowed_roots() -> list[str]:
    """Parse allowed roots from server-side configuration."""
    raw = getattr(Config, "SEARCH_ALLOWED_ROOTS", "") or ""
    roots = []
    for item in raw.split(","):
        candidate = item.strip().rstrip("/")
        if not candidate:
            continue
        if _ABSOLUTE_ROOT_RE.match(candidate) and ".." not in candidate.split("/"):
            roots.append(candidate)
        else:
            logger.warning("search_root_ignored value=%r", item)
    return roots


def ensure_patterns_registered() -> list[str]:
    """Register whitelist patterns for each configured root (idempotent).

    Patterns are derived ONLY from server config. Called lazily before the
    first search and at startup; safe to call repeatedly. For each root this
    registers the grep template plus every tail/depth bucket variant, so a
    request can only ever pick from pre-approved shapes.
    """
    global _registration_done
    roots = get_allowed_roots()
    if _registration_done and roots == _registered_roots:
        return roots
    for root in roots:
        register_readonly_pattern(_SEARCH_TEMPLATE.replace("{root}", root))
        for depth in _DEPTH_BUCKETS:
            register_readonly_pattern(
                _LIST_TEMPLATE.replace("{root}", root).replace("{depth}", str(depth)))
        for lines in _TAIL_BUCKETS:
            register_readonly_pattern(
                _TAIL_TEMPLATE.replace("{root}", root).replace("{lines}", str(lines)))
    _registered_roots.clear()
    _registered_roots.extend(roots)
    _registration_done = True
    logger.info("search_patterns_registered roots=%s", roots)
    return roots


def _path_allowed(root: str, relative: str) -> bool:
    """Refuse traversal and secret files for a root-relative path."""
    relative = (relative or "").strip().lstrip("/")
    if not relative:
        return True
    if not _SAFE_RELATIVE_RE.match(relative):
        return False
    if any(part == ".." for part in relative.split("/")):
        return False
    parts = relative.split("/")
    for part in parts:
        if part in FORBIDDEN_FILE_NAMES or part.startswith(".env"):
            return False
    last = parts[-1].lower()
    if any(last.endswith(suffix) for suffix in FORBIDDEN_FILE_SUFFIXES):
        return False
    return True


def _path_suffix(relative: str) -> str:
    relative = (relative or "").strip().lstrip("/")
    return f"/{relative}" if relative else ""


def _validate_term(query: str) -> str | None:
    query = (query or "").strip()
    if not query or len(query) > 120:
        return None
    if not _SAFE_TERM_RE.match(query):
        return None
    return query


def _run(command: str) -> dict:
    from app.executor import run_command
    return run_command(command)


def _finalize(raw: dict, command: str) -> dict:
    if not raw.get("success"):
        return {"success": False, "error": raw.get("error", "search execution failed"),
                "command": command}
    lines = [redact_line(line, MAX_LINE_LENGTH) for line in str(raw.get("stdout", "")).splitlines()]
    return {"success": True, "command": command, "lines": lines[:MAX_MATCHES],
            "truncated": len(lines) > MAX_MATCHES, "redacted": True}


def _nearest_allowed_error(root: str) -> str:
    available = ", ".join(get_allowed_roots()) or "none configured"
    return f"root '{root}' is not an allowed search root (allowed: {available})"


def remote_search(query: str, root: str, relative_path: str = "") -> dict:
    """grep -rIn under an allowed root. Secret files are refused; output is redacted."""
    ensure_patterns_registered()
    if root not in get_allowed_roots():
        return {"success": False, "error": _nearest_allowed_error(root), "error_code": "not_allowed"}
    term = _validate_term(query)
    if term is None:
        return {"success": False, "error": "search term must be 1-120 safe characters", "error_code": "invalid_input"}
    if not _path_allowed(root, relative_path):
        return {"success": False, "error": "path is not allowed under the configured search roots", "error_code": "not_allowed"}
    command = (_SEARCH_TEMPLATE.replace("{root}", root)
               .replace("{path}", _path_suffix(relative_path))
               .replace("{query}", term))
    logger.info("remote_search root=%s term=%r", root, term)
    return _finalize(_run(command), command)


def remote_list(root: str, relative_path: str = "", max_depth: int = 3) -> dict:
    """find (files only) under an allowed root, bounded depth."""
    ensure_patterns_registered()
    if root not in get_allowed_roots():
        return {"success": False, "error": _nearest_allowed_error(root), "error_code": "not_allowed"}
    depth = min(int(max_depth), max(_DEPTH_BUCKETS))
    depth = max(depth, min(_DEPTH_BUCKETS))
    if not _path_allowed(root, relative_path):
        return {"success": False, "error": "path is not allowed under the configured search roots", "error_code": "not_allowed"}
    command = (_LIST_TEMPLATE.replace("{root}", root)
               .replace("{path}", _path_suffix(relative_path))
               .replace("{depth}", str(depth)))
    logger.info("remote_list root=%s depth=%s", root, depth)
    return _finalize(_run(command), command)


def remote_tail(root: str, relative_path: str, lines: int = 100) -> dict:
    """tail -n on a specific file under an allowed root. Secret files refused."""
    ensure_patterns_registered()
    if root not in get_allowed_roots():
        return {"success": False, "error": _nearest_allowed_error(root), "error_code": "not_allowed"}
    if not relative_path.strip():
        return {"success": False, "error": "a file path is required for tail", "error_code": "invalid_input"}
    bounded = min((bucket for bucket in _TAIL_BUCKETS if bucket >= max(1, int(lines))),
                  default=max(_TAIL_BUCKETS))
    if not _path_allowed(root, relative_path):
        return {"success": False, "error": "path is not allowed under the configured search roots", "error_code": "not_allowed"}
    command = (_TAIL_TEMPLATE.replace("{root}", root)
               .replace("{path}", _path_suffix(relative_path))
               .replace("{lines}", str(bounded)))
    logger.info("remote_tail root=%s path=%s lines=%s", root, relative_path, bounded)
    return _finalize(_run(command), command)
