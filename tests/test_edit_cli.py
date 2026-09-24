"""Tests for the safe file-editing CLI (``scripts/edit.py``)."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "edit_cli", Path(__file__).resolve().parents[1] / "scripts" / "edit.py"
)
assert _SPEC and _SPEC.loader
edit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(edit)


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "config.py"
    path.write_text("DEBUG = False\nPORT = 8000\n")
    return path


def _backups(path: Path) -> list[Path]:
    directory = path.parent / edit.BACKUP_DIR_NAME
    if not directory.is_dir():
        return []
    return sorted(directory.iterdir())


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_find_match_lines_reports_every_occurrence():
    text = "one\ntwo one\nthree\n"
    assert edit.find_match_lines(text, "one") == [1, 2]
    assert edit.find_match_lines(text, "missing") == []


def test_unescape_expands_escapes():
    assert edit.unescape("a\\nb\\tc\\\\d") == "a\nb\tc\\d"
    assert edit.unescape("100%") == "100%"
    assert edit.unescape("trailing\\") == "trailing\\"


# --------------------------------------------------------------------------- #
# replace
# --------------------------------------------------------------------------- #


def test_replace_single_match_succeeds_with_backup_and_diff(target, capsys):
    original = target.read_text()
    assert edit.main(["replace", str(target), "--old", "DEBUG = False", "--new", "DEBUG = True"]) == 0
    assert target.read_text() == "DEBUG = True\nPORT = 8000\n"

    backups = _backups(target)
    assert len(backups) == 1
    assert backups[0].read_text() == original
    assert target.name in backups[0].name

    out = capsys.readouterr().out
    assert "replaced 1 occurrence at line 1" in out
    assert "-DEBUG = False" in out
    assert "+DEBUG = True" in out


def test_replace_zero_matches_refuses_cleanly(target, capsys):
    original = target.read_text()
    assert edit.main(["replace", str(target), "--old", "NOPE = 1", "--new", "X"]) == 1
    assert target.read_text() == original
    assert _backups(target) == []
    assert "--old was not found" in capsys.readouterr().err


def test_replace_multiple_matches_refuses_and_lists_lines(tmp_path, capsys):
    path = tmp_path / "dup.py"
    path.write_text("port = 1\nport = 2\nport = 3\n")
    original = path.read_text()

    assert edit.main(["replace", str(path), "--old", "port = ", "--new", "listen = "]) == 1

    err = capsys.readouterr().err
    assert "matches 3 times" in err
    assert "line 1, line 2, line 3" in err
    assert path.read_text() == original
    assert _backups(path) == []


def test_replace_multiline_via_old_file_and_new_file(tmp_path, capsys):
    path = tmp_path / "block.txt"
    path.write_text("start\nalpha\nbeta\nend\n")
    old_file = tmp_path / "old.txt"
    new_file = tmp_path / "new.txt"
    old_file.write_text("alpha\nbeta\n")
    new_file.write_text("alpha\ngamma\nbeta\n")

    assert edit.main(["replace", str(path), "--old-file", str(old_file), "--new-file", str(new_file)]) == 0
    assert path.read_text() == "start\nalpha\ngamma\nbeta\nend\n"


def test_replace_unescape_flag_handles_newlines_in_arguments(target):
    assert (
        edit.main(
            [
                "replace",
                str(target),
                "--old",
                "DEBUG = False\\nPORT = 8000",
                "--new",
                "DEBUG = False\\nPORT = 9000",
                "--unescape",
            ]
        )
        == 0
    )
    assert target.read_text() == "DEBUG = False\nPORT = 9000\n"


def test_replace_dry_run_writes_nothing(target, capsys):
    original = target.read_text()
    assert edit.main(["replace", str(target), "--old", "PORT = 8000", "--new", "PORT = 9000", "--dry-run"]) == 0
    assert target.read_text() == original
    assert _backups(target) == []
    assert "dry-run" in capsys.readouterr().out


def test_replace_identical_old_and_new_refused(target, capsys):
    assert edit.main(["replace", str(target), "--old", "DEBUG", "--new", "DEBUG"]) == 1
    assert "identical" in capsys.readouterr().err


def test_replace_empty_old_refused(target, capsys):
    assert edit.main(["replace", str(target), "--old", "", "--new", "x"]) == 2
    assert "must not be empty" in capsys.readouterr().err


def test_replace_missing_file_refused(tmp_path, capsys):
    assert edit.main(["replace", str(tmp_path / "nope.py"), "--old", "a", "--new", "b"]) == 1
    assert "file not found" in capsys.readouterr().err


def test_replace_directory_refused(tmp_path, capsys):
    assert edit.main(["replace", str(tmp_path), "--old", "a", "--new", "b"]) == 1
    assert "file not found" in capsys.readouterr().err


def test_replace_crlf_file_gets_hint(tmp_path, capsys):
    path = tmp_path / "win.py"
    path.write_bytes(b"a = 1\r\nb = 2\r\n")
    assert edit.main(["replace", str(path), "--old", "a = 1\nb = 2", "--new", "x"]) == 1
    assert "CRLF" in capsys.readouterr().err


def test_replace_preserves_crlf_line_endings(tmp_path):
    path = tmp_path / "win.py"
    path.write_bytes(b"a = 1\r\nb = 2\r\n")
    assert edit.main(["replace", str(path), "--old", "b = 2", "--new", "b = 3"]) == 0
    assert path.read_bytes() == b"a = 1\r\nb = 3\r\n"


# --------------------------------------------------------------------------- #
# atomic writes and undo
# --------------------------------------------------------------------------- #


def test_failed_write_leaves_original_intact(target, monkeypatch, capsys):
    original = target.read_text()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(edit.os, "replace", boom)
    assert edit.main(["replace", str(target), "--old", "PORT = 8000", "--new", "PORT = 1"]) == 2

    assert target.read_text() == original
    assert "could not write" in capsys.readouterr().err
    monkeypatch.undo()
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []
    assert len(_backups(target)) == 1


def test_undo_round_trip_restores_original(target, capsys):
    original = target.read_text()
    edit.main(["replace", str(target), "--old", "DEBUG = False", "--new", "DEBUG = True"])
    capsys.readouterr()

    assert edit.main(["undo", str(target)]) == 0
    assert target.read_text() == original
    assert "previous content archived" in capsys.readouterr().out

    used = [p for p in _backups(target) if p.name.endswith(".undone")]
    assert len(used) == 1
    assert used[0].read_text() == original


def test_undo_without_backup_refused(target, capsys):
    assert edit.main(["undo", str(target)]) == 1
    assert "no backups found" in capsys.readouterr().err


def test_undo_dry_run_writes_nothing(target, capsys):
    edit.main(["replace", str(target), "--old", "PORT = 8000", "--new", "PORT = 9000"])
    changed = target.read_text()
    capsys.readouterr()

    assert edit.main(["undo", str(target), "--dry-run"]) == 0
    assert target.read_text() == changed
    assert len(_backups(target)) == 1
    assert "dry-run" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# view
# --------------------------------------------------------------------------- #


def test_view_prints_line_numbers_and_range(target, capsys):
    assert edit.main(["view", str(target)]) == 0
    out = capsys.readouterr().out
    assert str(target) in out
    assert "DEBUG = False" in out and "PORT = 8000" in out

    assert edit.main(["view", str(target), "--start", "2", "--end", "2"]) == 0
    out = capsys.readouterr().out
    assert "PORT = 8000" in out
    assert "DEBUG = False" not in out


def test_view_directory_lists_entries(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("hi\n")
    assert edit.main(["view", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "sub/" in out
    assert "file.txt" in out


def test_view_missing_path_refused(tmp_path, capsys):
    assert edit.main(["view", str(tmp_path / "nothing")]) == 1
    assert "path not found" in capsys.readouterr().err


def test_view_binary_file_refused(tmp_path, capsys):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x00\x01\x02")
    assert edit.main(["view", str(path)]) == 2
    assert "binary" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# create / append
# --------------------------------------------------------------------------- #


def test_create_from_stdin(tmp_path, monkeypatch, capsys):
    path = tmp_path / "new.py"
    monkeypatch.setattr("sys.stdin", io.StringIO("print('hi')"))
    assert edit.main(["create", str(path)]) == 0
    assert path.read_text() == "print('hi')\n"
    assert "created" in capsys.readouterr().out


def test_create_refuses_existing_file(target, monkeypatch, capsys):
    original = target.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO("replacement"))
    assert edit.main(["create", str(target)]) == 1
    assert target.read_text() == original
    assert "already exists" in capsys.readouterr().err


def test_create_force_overwrites_and_backs_up(target, monkeypatch, capsys):
    original = target.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO("brand new\n"))
    assert edit.main(["create", str(target), "--force"]) == 0
    assert target.read_text() == "brand new\n"
    assert [b.read_text() for b in _backups(target)] == [original]
    assert "backed up" in capsys.readouterr().out


def test_create_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ghost.py"
    monkeypatch.setattr("sys.stdin", io.StringIO("body\n"))
    assert edit.main(["create", str(path), "--dry-run"]) == 0
    assert not path.exists()
    assert "dry-run" in capsys.readouterr().out


def test_create_requires_parents_flag(tmp_path, monkeypatch, capsys):
    path = tmp_path / "pkg" / "mod.py"
    monkeypatch.setattr("sys.stdin", io.StringIO("body\n"))
    assert edit.main(["create", str(path)]) == 1
    assert "parent directory" in capsys.readouterr().err
    assert not path.exists()

    monkeypatch.setattr("sys.stdin", io.StringIO("body\n"))
    assert edit.main(["create", str(path), "--parents"]) == 0
    assert path.read_text() == "body\n"


def test_append_adds_content_and_counts_lines(target, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("NEW = 1\n"))
    assert edit.main(["append", str(target)]) == 0
    assert target.read_text() == "DEBUG = False\nPORT = 8000\nNEW = 1\n"
    assert "appended 1 lines" in capsys.readouterr().out


def test_append_inserts_missing_newline_separator(tmp_path, monkeypatch, capsys):
    path = tmp_path / "notes.md"
    path.write_text("no trailing newline")
    monkeypatch.setattr("sys.stdin", io.StringIO("second line\n"))
    assert edit.main(["append", str(path)]) == 0
    assert path.read_text() == "no trailing newline\nsecond line\n"
    assert "newline separator" in capsys.readouterr().out


def test_append_missing_file_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("data\n"))
    assert edit.main(["append", str(tmp_path / "nope.txt")]) == 1
    assert "use `create`" in capsys.readouterr().err


def test_append_empty_stdin_is_an_error(target, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert edit.main(["append", str(target)]) == 2
    assert "nothing on stdin" in capsys.readouterr().err


def test_append_dry_run_writes_nothing(target, monkeypatch, capsys):
    original = target.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO("X = 1\n"))
    assert edit.main(["append", str(target), "--dry-run"]) == 0
    assert target.read_text() == original
    assert _backups(target) == []


# --------------------------------------------------------------------------- #
# diff / grep
# --------------------------------------------------------------------------- #


def test_diff_reports_changes(tmp_path, capsys):
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("a\nb\n")
    right.write_text("a\nc\n")
    assert edit.main(["diff", str(left), str(right)]) == 0
    out = capsys.readouterr().out
    assert "-b" in out and "+c" in out


def test_diff_identical_files(tmp_path, capsys):
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("same\n")
    right.write_text("same\n")
    assert edit.main(["diff", str(left), str(right)]) == 0
    assert "identical" in capsys.readouterr().out


def test_grep_recursive_finds_matches(tmp_path, capsys):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("EXECUTION_MODE = 'ssh'\n")
    (tmp_path / "pkg" / "b.py").write_text("nothing here\n")
    assert edit.main(["grep", "EXECUTION_MODE", str(tmp_path), "--recursive"]) == 0
    out = capsys.readouterr().out
    assert "a.py" in out
    assert ":1:" in out
    assert "b.py" not in out


def test_grep_no_match_returns_refused(tmp_path, capsys):
    (tmp_path / "a.py").write_text("hello\n")
    assert edit.main(["grep", "zzz", str(tmp_path)]) == 1
    assert "no matches" in capsys.readouterr().out


def test_grep_fixed_and_ignore_case(tmp_path, capsys):
    (tmp_path / "a.py").write_text("Value = config.get('x')\n")
    assert edit.main(["grep", "config.get('x')", str(tmp_path), "--fixed"]) == 0
    assert "a.py" in capsys.readouterr().out

    assert edit.main(["grep", "value", str(tmp_path), "--ignore-case"]) == 0
    assert "a.py" in capsys.readouterr().out


def test_grep_invalid_pattern_is_an_error(tmp_path, capsys):
    (tmp_path / "a.py").write_text("hello\n")
    assert edit.main(["grep", "(", str(tmp_path)]) == 2
    assert "invalid pattern" in capsys.readouterr().err


def test_grep_skips_backup_and_vcs_directories(tmp_path, capsys):
    (tmp_path / ".edit_backups").mkdir()
    (tmp_path / ".edit_backups" / "old.py").write_text("SECRET_TOKEN = 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("SECRET_TOKEN = 2\n")
    (tmp_path / "live.py").write_text("SECRET_TOKEN = 3\n")

    assert edit.main(["grep", "SECRET_TOKEN", str(tmp_path), "--recursive"]) == 0
    out = capsys.readouterr().out
    assert "live.py" in out
    assert "old.py" not in out
    assert ".git" not in out


def test_grep_respects_max_matches(tmp_path, capsys):
    (tmp_path / "many.txt").write_text("\n".join(f"hit {i}" for i in range(10)) + "\n")
    assert edit.main(["grep", "hit", str(tmp_path), "--max", "3"]) == 0
    out = capsys.readouterr().out
    assert out.count("hit ") == 3
    assert "truncated at 3 matches" in out


def test_grep_missing_path_refused(tmp_path, capsys):
    assert edit.main(["grep", "x", str(tmp_path / "missing")]) == 1
    assert "path not found" in capsys.readouterr().err
