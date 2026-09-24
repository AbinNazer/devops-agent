#!/usr/bin/env python3
"""Safe, scriptable file editing for the JARVIS repository.

Unlike ``sed``, every edit is verified: ``replace`` refuses to touch a file
unless the exact ``--old`` string occurs **exactly once**, so an ambiguous or
missing pattern can never silently rewrite the wrong line.

Examples
--------
    python scripts/edit.py view app/config.py --start 40 --end 80
    python scripts/edit.py replace app/config.py --old "DEBUG = False" --new "DEBUG = True"
    python scripts/edit.py replace app/config.py --old-file old.txt --new-file new.txt
    python scripts/edit.py replace app/config.py --old 'a\nb' --new 'c' --unescape --dry-run
    python scripts/edit.py create app/tools_cli/note.py << 'EOF'
    ...file body...
    EOF
    python scripts/edit.py append logs/notes.md << 'EOF'
    ...text to append...
    EOF
    python scripts/edit.py diff app/config.py app/config.py.orig
    python scripts/edit.py undo app/config.py
    python scripts/edit.py grep EXECUTION_MODE app --recursive

Alias it for daily use::

    alias edit="python /path/to/devops-agent/scripts/edit.py"

Safety guarantees
-----------------
* Every write is backed up first to ``.edit_backups/<timestamp>_<filename>``
  next to the edited file; ``undo`` restores the newest backup.
* Writes are atomic: content goes to a temp file in the same directory and is
  then moved into place, so a crash mid-write cannot truncate the original.
* No shell is ever invoked (``subprocess`` is not used); all search is done
  with the standard library, so behaviour is identical on Linux, WSL, macOS
  and Windows.

Exit codes: ``0`` success, ``1`` refused (no match / ambiguous / missing file),
``2`` usage or I/O error.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BACKUP_DIR_NAME = ".edit_backups"
DEFAULT_CONTEXT = 3
DEFAULT_MAX_MATCHES = 200

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

# Directories skipped by recursive grep (never worth searching).
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".freebuff",
    BACKUP_DIR_NAME,
}

_COLOR = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}


class EditError(Exception):
    """A user-facing failure with an explicit process exit code."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #


def use_color(explicit: bool | None = None) -> bool:
    """Colour only on a real terminal, and honour the NO_COLOR convention."""
    if explicit is not None:
        return explicit
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def paint(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLOR.get(color, '')}{text}{_COLOR['reset']}"


def read_text(path: Path) -> str:
    """Read a text file byte-exactly (line endings are preserved).

    Decoding then re-encoding UTF-8 round-trips CRLF untouched, so an edit
    never silently rewrites every line of a Windows-checked-out file.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise EditError(f"file not found: {path}", EXIT_REFUSED) from None
    except IsADirectoryError:
        raise EditError(f"not a file: {path}", EXIT_REFUSED) from None
    except OSError as exc:
        raise EditError(f"could not read {path}: {exc}", EXIT_ERROR) from None
    if b"\x00" in data:
        raise EditError(f"{path} looks like a binary file; refusing to edit", EXIT_ERROR)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditError(f"{path} is not valid UTF-8 text: {exc}", EXIT_ERROR) from None


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + rename (never partial)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = 0o644
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise EditError(f"could not write {path}: {exc}", EXIT_ERROR) from None
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def backup_file(path: Path) -> Path:
    """Copy ``path`` into ``.edit_backups/<timestamp>_<filename>``."""
    backup_dir = path.parent / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    candidate = backup_dir / f"{stamp}_{path.name}"
    counter = 1
    while candidate.exists():
        candidate = backup_dir / f"{stamp}_{counter}_{path.name}"
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def latest_backup(path: Path) -> Path | None:
    """Newest backup for ``path`` that has not already been undone."""
    backup_dir = path.parent / BACKUP_DIR_NAME
    if not backup_dir.is_dir():
        return None
    candidates = [
        entry
        for entry in backup_dir.iterdir()
        if entry.is_file() and entry.name.endswith(f"_{path.name}") and not entry.name.endswith(".undone")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: (entry.stat().st_mtime_ns, entry.name))


def find_match_lines(text: str, needle: str) -> list[int]:
    """1-based line number of every non-overlapping occurrence of ``needle``."""
    lines: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            break
        lines.append(text.count("\n", 0, index) + 1)
        start = index + len(needle)
    return lines


def crlf_hint(text: str, needle: str) -> str:
    """Explain a zero-match caused purely by CRLF vs LF line endings."""
    if "\r\n" not in text or "\n" not in needle or "\r" in needle:
        return ""
    if needle.replace("\n", "\r\n") in text:
        return " (hint: this file uses CRLF line endings - retry with \\r\\n or --unescape)"
    return ""


def unescape(value: str) -> str:
    """Interpret the backslash escapes that are awkward to pass through a shell."""
    out: list[str] = []
    index = 0
    mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "0": "\0"}
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value) and value[index + 1] in mapping:
            out.append(mapping[value[index + 1]])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def unified_diff(
    before: str,
    after: str,
    fromfile: str,
    tofile: str,
    context: int = DEFAULT_CONTEXT,
    color: bool = False,
) -> list[str]:
    """Colourised unified diff lines."""
    raw = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=fromfile,
        tofile=tofile,
        n=context,
        lineterm="",
    )
    rendered: list[str] = []
    for line in raw:
        if line.startswith("---") or line.startswith("+++"):
            rendered.append(paint(line, "bold", color))
        elif line.startswith("@@"):
            rendered.append(paint(line, "cyan", color))
        elif line.startswith("+"):
            rendered.append(paint(line, "green", color))
        elif line.startswith("-"):
            rendered.append(paint(line, "red", color))
        else:
            rendered.append(line)
    return rendered


def emit_diff(
    before: str,
    after: str,
    fromfile: str,
    tofile: str,
    context: int = DEFAULT_CONTEXT,
    color: bool = False,
) -> None:
    for line in unified_diff(before, after, fromfile, tofile, context, color):
        print(line)


def read_stdin() -> str:
    stream = sys.stdin
    try:
        interactive = stream.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        raise EditError(
            "no content on stdin; pipe or redirect it (e.g. `cmd << 'EOF' ... EOF`)", EXIT_ERROR
        )
    return stream.read()


def _iter_files(root: Path, recursive: bool):
    if root.is_file():
        yield root
        return
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name
    else:
        for entry in sorted(root.iterdir()):
            if entry.is_file():
                yield entry


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_view(args: argparse.Namespace) -> int:
    path = Path(args.path)
    color = use_color(getattr(args, "color", None))
    if path.is_dir():
        print(paint(f"{path}/", "bold", color))
        entries = sorted(path.iterdir(), key=lambda entry: (entry.is_file(), entry.name.lower()))
        for entry in entries:
            try:
                info = entry.stat()
            except OSError:
                continue
            mode = stat.filemode(info.st_mode)
            stamp = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
            name = entry.name + ("/" if entry.is_dir() else "")
            if entry.is_dir():
                name = paint(name, "blue", color)
            kind = "dir " if entry.is_dir() else "file"
            print(f"{mode}  {kind}  {info.st_size:>10}  {stamp}  {name}")
        return EXIT_OK
    if not path.exists():
        raise EditError(f"path not found: {path}", EXIT_REFUSED)

    text = read_text(path)
    lines = text.splitlines()
    start = args.start or 1
    end = args.end or len(lines)
    if start < 1 or end < start:
        raise EditError(f"invalid range: start={start} end={end}", EXIT_ERROR)
    print(paint(f"== {path} ({len(lines)} lines) ==", "bold", color))
    for number in range(start, min(end, len(lines)) + 1):
        gutter = paint(f"{number:>6}", "cyan", color)
        print(f"{gutter}\t{lines[number - 1]}")
    return EXIT_OK


def _resolve_replacement(args: argparse.Namespace) -> tuple[str, str]:
    if args.old_file:
        old = read_text(Path(args.old_file))
    else:
        old = args.old or ""
        if args.unescape:
            old = unescape(old)
    if args.new_file:
        new = read_text(Path(args.new_file))
    else:
        new = args.new if args.new is not None else ""
        if args.unescape:
            new = unescape(new)
    return old, new


def cmd_replace(args: argparse.Namespace) -> int:
    path = Path(args.path)
    color = use_color(getattr(args, "color", None))
    if not path.is_file():
        raise EditError(f"file not found: {path}", EXIT_REFUSED)

    old, new = _resolve_replacement(args)
    if old == "":
        raise EditError("--old must not be empty (use --old-file for multi-line blocks)", EXIT_ERROR)
    if old == new:
        raise EditError("--old and --new are identical; nothing to do", EXIT_REFUSED)

    text = read_text(path)
    matches = find_match_lines(text, old)
    if not matches:
        raise EditError(f"--old was not found in {path}{crlf_hint(text, old)}", EXIT_REFUSED)
    if len(matches) > 1:
        where = ", ".join(f"line {line}" for line in matches)
        raise EditError(
            f"--old matches {len(matches)} times in {path} ({where}); "
            "include more surrounding context so it matches exactly once",
            EXIT_REFUSED,
        )

    line = matches[0]
    updated = text.replace(old, new, 1)
    if args.dry_run:
        print(paint(f"dry-run: would replace 1 occurrence at line {line} in {path}", "yellow", color))
        emit_diff(text, updated, f"a/{path}", f"b/{path}", args.context, color)
        return EXIT_OK

    backup = backup_file(path)
    atomic_write(path, updated)
    print(
        paint(f"replaced 1 occurrence at line {line} in {path}", "green", color)
        + f"\nbackup: {backup}"
    )
    emit_diff(text, updated, f"a/{path}", f"b/{path}", args.context, color)
    return EXIT_OK


def cmd_create(args: argparse.Namespace) -> int:
    path = Path(args.path)
    color = use_color(getattr(args, "color", None))
    content = read_stdin()
    if path.exists() and not args.force:
        raise EditError(
            f"{path} already exists; use `replace`/`append`, or --force to overwrite",
            EXIT_REFUSED,
        )
    if content and not content.endswith("\n"):
        content += "\n"
    if args.dry_run:
        print(paint(f"dry-run: would create {path} ({content.count(chr(10))} lines)", "yellow", color))
        return EXIT_OK
    if path.exists():
        backup = backup_file(path)
        note = f" (previous content backed up to {backup})"
    else:
        note = ""
    if not path.parent.exists():
        if not args.parents:
            raise EditError(
                f"parent directory does not exist: {path.parent} (pass --parents to create it)",
                EXIT_REFUSED,
            )
    atomic_write(path, content)
    print(paint(f"created {path} ({content.count(chr(10))} lines)", "green", color) + note)
    return EXIT_OK


def cmd_append(args: argparse.Namespace) -> int:
    path = Path(args.path)
    color = use_color(getattr(args, "color", None))
    if not path.is_file():
        raise EditError(f"file not found: {path} (use `create` for new files)", EXIT_REFUSED)
    addition = read_stdin()
    if not addition:
        raise EditError("nothing on stdin to append", EXIT_ERROR)

    text = read_text(path)
    updated = text
    separator = ""
    if text and not text.endswith("\n"):
        separator = "\n"
    updated = f"{text}{separator}{addition}"
    if addition and not updated.endswith("\n"):
        updated += "\n"

    added_lines = addition.count("\n") + (0 if addition.endswith("\n") else 1)
    if args.dry_run:
        print(
            paint(
                f"dry-run: would append {added_lines} lines to {path}"
                + (" (inserting a missing newline separator)" if separator else ""),
                "yellow",
                color,
            )
        )
        emit_diff(text, updated, f"a/{path}", f"b/{path}", args.context, color)
        return EXIT_OK

    backup = backup_file(path)
    atomic_write(path, updated)
    print(
        paint(f"appended {added_lines} lines to {path}", "green", color)
        + (" (inserted a missing newline separator)" if separator else "")
        + f"\nbackup: {backup}"
    )
    return EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    color = use_color(getattr(args, "color", None))
    left, right = Path(args.path1), Path(args.path2)
    before, after = read_text(left), read_text(right)
    if before == after:
        print(paint(f"no differences: {left} and {right} are identical", "yellow", color))
        return EXIT_OK
    emit_diff(before, after, str(left), str(right), args.context, color)
    return EXIT_OK


def cmd_undo(args: argparse.Namespace) -> int:
    path = Path(args.path)
    color = use_color(getattr(args, "color", None))
    backup = latest_backup(path)
    if backup is None:
        raise EditError(f"no backups found for {path} in {path.parent / BACKUP_DIR_NAME}", EXIT_REFUSED)
    restored = read_text(backup)

    if args.dry_run:
        current = read_text(path) if path.is_file() else ""
        print(paint(f"dry-run: would restore {backup} over {path}", "yellow", color))
        emit_diff(current, restored, f"a/{path}", f"b/{path}", args.context, color)
        return EXIT_OK

    archived = backup_file(path) if path.is_file() else None
    atomic_write(path, restored)
    backup.rename(backup.with_name(backup.name + ".undone"))
    print(paint(f"restored {path} from {backup}", "green", color))
    if archived is not None:
        print(f"previous content archived at {archived}")
    return EXIT_OK


def cmd_grep(args: argparse.Namespace) -> int:
    color = use_color(getattr(args, "color", None))
    root = Path(args.path)
    if not root.exists():
        raise EditError(f"path not found: {root}", EXIT_REFUSED)
    flags = re.IGNORECASE if args.ignore_case else 0
    if args.fixed:
        pattern = re.compile(re.escape(args.pattern), flags)
    else:
        try:
            pattern = re.compile(args.pattern, flags)
        except re.error as exc:
            raise EditError(f"invalid pattern: {exc}", EXIT_ERROR) from None

    hits = 0
    truncated = False
    for file_path in _iter_files(root, args.recursive):
        try:
            data = file_path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if not pattern.search(line):
                continue
            hits += 1
            if hits > args.max:
                truncated = True
                break
            print(f"{paint(str(file_path), 'bold', color)}:{paint(str(number), 'cyan', color)}:{line}")
        if truncated:
            break
    if hits == 0:
        print(paint(f"no matches for {args.pattern!r} in {root}", "yellow", color))
        return EXIT_REFUSED
    if truncated:
        print(paint(f"... truncated at {args.max} matches", "yellow", color))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-color", dest="color", action="store_false", default=None,
                        help="disable colourised output")
    parser.add_argument("--color", dest="color", action="store_true",
                        help="force colourised output even when piped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edit.py",
        description="Safe, verified file editing (exact-match replace, backups, atomic writes).",
        epilog="Exit codes: 0 success, 1 refused (no/ambiguous match, missing file), 2 usage or I/O error.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    view = sub.add_parser("view", help="print a file with line numbers, or list a directory")
    view.add_argument("path")
    view.add_argument("--start", type=int, default=None, help="first line to show (1-based)")
    view.add_argument("--end", type=int, default=None, help="last line to show (inclusive)")
    _add_common(view)
    view.set_defaults(func=cmd_view)

    replace = sub.add_parser("replace", help="replace an exact string that must match exactly once")
    replace.add_argument("path")
    old_group = replace.add_mutually_exclusive_group(required=True)
    old_group.add_argument("--old", default=None, help="exact string to find")
    old_group.add_argument("--old-file", default=None, help="file containing the exact string to find")
    new_group = replace.add_mutually_exclusive_group(required=True)
    new_group.add_argument("--new", default=None, help="replacement string (may be empty to delete)")
    new_group.add_argument("--new-file", default=None, help="file containing the replacement string")
    replace.add_argument("--unescape", action="store_true",
                         help="interpret \\n, \\t, \\r, \\\\ in --old/--new")
    replace.add_argument("--dry-run", action="store_true", help="show the change without writing")
    replace.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="diff context lines")
    _add_common(replace)
    replace.set_defaults(func=cmd_replace)

    create = sub.add_parser("create", help="create a file from stdin")
    create.add_argument("path")
    create.add_argument("--force", action="store_true", help="overwrite an existing file (backs it up)")
    create.add_argument("--parents", action="store_true", help="create missing parent directories")
    create.add_argument("--dry-run", action="store_true", help="show what would be written")
    _add_common(create)
    create.set_defaults(func=cmd_create)

    append = sub.add_parser("append", help="append stdin to an existing file")
    append.add_argument("path")
    append.add_argument("--dry-run", action="store_true", help="show what would be appended")
    append.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="diff context lines")
    _add_common(append)
    append.set_defaults(func=cmd_append)

    diff = sub.add_parser("diff", help="unified diff between two files")
    diff.add_argument("path1")
    diff.add_argument("path2")
    diff.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="diff context lines")
    _add_common(diff)
    diff.set_defaults(func=cmd_diff)

    undo = sub.add_parser("undo", help="restore the newest backup of a file")
    undo.add_argument("path")
    undo.add_argument("--dry-run", action="store_true", help="show the restore without writing")
    undo.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="diff context lines")
    _add_common(undo)
    undo.set_defaults(func=cmd_undo)

    grep = sub.add_parser("grep", help="search a file or directory (stdlib, no external binaries)")
    grep.add_argument("pattern")
    grep.add_argument("path")
    grep.add_argument("--recursive", "-r", action="store_true", help="walk directories recursively")
    grep.add_argument("--ignore-case", "-i", action="store_true", help="case-insensitive search")
    grep.add_argument("--fixed", "-F", action="store_true", help="treat the pattern as a literal string")
    grep.add_argument("--max", type=int, default=DEFAULT_MAX_MATCHES, help="maximum matches to print")
    _add_common(grep)
    grep.set_defaults(func=cmd_grep)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except EditError as exc:
        prefix = "refused: " if exc.code == EXIT_REFUSED else "error: "
        print(f"{prefix}{exc.message}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
