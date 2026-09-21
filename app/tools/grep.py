"""Bounded, read-only project search without arbitrary shell execution."""
from __future__ import annotations

import fnmatch
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("tools.grep")
MAX_FILE_BYTES = 2_000_000
MAX_RESULTS = 200
MAX_LINE_LENGTH = 500
SECRET_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache"}
SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|password|secret|private[_-]?key)(\s*[:=]\s*)[^\s,;]+")


def _safe_root(path: str | None) -> Path:
    root = Path(path or ".").expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Search path must be an existing directory")
    return root


def _redact(line: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2[REDACTED]", line)


def grep_search(query: str, path: str = ".", file_pattern: str = "", context_lines: int = 0, max_results: int = 100, regex: bool = False) -> dict:
    """Search text files under a bounded directory; never executes a command."""
    if not query or len(query) > 300:
        return {"success": False, "error": "query is required and must be at most 300 characters", "matches": []}
    try:
        root = _safe_root(path)
        limit = max(1, min(int(max_results), MAX_RESULTS))
        context = max(0, min(int(context_lines), 5))
        matcher = re.compile(query, re.IGNORECASE) if regex else None
    except (ValueError, re.error) as exc:
        return {"success": False, "error": str(exc), "matches": []}

    matches = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for filename in files:
            if filename in SECRET_NAMES or filename.startswith(".env"):
                continue
            if file_pattern and not fnmatch.fnmatch(filename, file_pattern):
                continue
            full = Path(current) / filename
            try:
                if full.stat().st_size > MAX_FILE_BYTES or full.suffix.lower() in {".db", ".sqlite", ".pyc", ".png", ".jpg", ".zip", ".gz"}:
                    continue
                lines = full.read_text(encoding="utf-8", errors="strict").splitlines()
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(lines):
                found = bool(matcher.search(line)) if matcher else query.casefold() in line.casefold()
                if not found:
                    continue
                start, end = max(0, number - context), min(len(lines), number + context + 1)
                matches.append({"file": str(full.relative_to(root)), "line": number + 1, "text": _redact(line[:MAX_LINE_LENGTH]), "context": [_redact(x[:MAX_LINE_LENGTH]) for x in lines[start:end]]})
                if len(matches) >= limit:
                    return {"success": True, "query": query, "root": str(root), "matches": matches, "truncated": True}
    logger.info("grep_search_completed query=%r matches=%d", query, len(matches))
    return {"success": True, "query": query, "root": str(root), "matches": matches, "truncated": False}
