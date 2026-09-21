"""Secret redaction shared by Tool Factory execution and remote search.

Values are removed while key names are preserved, so outputs stay useful
for diagnosis without leaking credentials.
"""
from __future__ import annotations

import re

# Keyed assignments: password=..., "api_key": "...", TOKEN: x
# The value match tolerates surrounding quotes and consumes them.
KEY_VALUE_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey|private[_-]?key|"
    r"access[_-]?key|authorization|credential|database[_-]?url|dsn)"
    r"(\s*[:=]\s*)(\"[^\"\s]*\"|'[^'\s]*'|[^\s,;'\"&]+)"
)

# PEM blocks — never emit these.
_PEM_PATTERN = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.S)

# Common connection strings with inline credentials.
_URL_CREDENTIALS_PATTERN = re.compile(
    r"(?i)\b(\w+)://[^/\s:]+:[^@\s/]+@"
)

REDACTED = "<redacted>"


def redact(text: str, max_bytes: int | None = None) -> str:
    """Redact secret-looking values from text, preserving key names.

    If max_bytes is provided, output is truncated to that byte budget.
    """
    if not text:
        return text
    redacted = _PEM_PATTERN.sub("<private-key-redacted>", text)
    redacted = KEY_VALUE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", redacted)
    redacted = _URL_CREDENTIALS_PATTERN.sub(lambda m: f"{m.group(1)}://{REDACTED}:***@", redacted)
    if max_bytes is not None and len(redacted.encode("utf-8", errors="replace")) > max_bytes:
        # Cut on a character boundary under the byte budget.
        cut = max_bytes
        encoded = redacted.encode("utf-8", errors="replace")
        while cut > 0:
            try:
                encoded[:cut].decode("utf-8")
                redacted = encoded[:cut].decode("utf-8", errors="replace")
                break
            except UnicodeDecodeError:
                cut -= 1
        redacted += "\n...[truncated]"
    return redacted


def redact_line(line: str, max_length: int = 500) -> str:
    """Redact and clip a single log/config line."""
    return redact(line[:max_length])
