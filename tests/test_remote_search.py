"""Remote search security tests."""
import pytest

from app.tool_factory import remote_search as rs
from app.ssh_whitelist import is_command_allowed, registered_patterns_snapshot


@pytest.fixture(autouse=True)
def configured_roots(monkeypatch):
    from app.config import Config
    monkeypatch.setattr(Config, "SEARCH_ALLOWED_ROOTS", "/var/www,/etc/nginx,/var/../evil")
    rs._registration_done = False
    rs._registered_roots.clear()
    yield
    rs._registration_done = False
    rs._registered_roots.clear()


class TestRootParsing:
    def test_roots_parsed_and_sanitized(self):
        roots = rs.get_allowed_roots()
        assert "/var/www" in roots and "/etc/nginx" in roots
        assert "/var/../evil" not in roots  # traversal root refused

    def test_empty_config_means_no_roots(self, monkeypatch):
        from app.config import Config
        monkeypatch.setattr(Config, "SEARCH_ALLOWED_ROOTS", "")
        assert rs.get_allowed_roots() == []


class TestWhitelistRegistration:
    def test_patterns_registered_from_config(self):
        rs.ensure_patterns_registered()
        # Behavioral check: a search command with the configured root baked in
        # and a safe query in the slot must pass the whitelist.
        assert is_command_allowed("grep -rIn -m 50 -e admin /var/www")
        assert is_command_allowed("grep -rIn -m 50 -e upstream /etc/nginx")
        assert is_command_allowed("find /var/www -maxdepth 3 -type f")
        assert is_command_allowed("tail -n 100 /var/www/logs/app.log")

    def test_registration_is_idempotent(self):
        roots = rs.ensure_patterns_registered()
        before = len(registered_patterns_snapshot())
        rs.ensure_patterns_registered()
        assert len(registered_patterns_snapshot()) == before
        # Per root: 1 grep + 4 depth buckets + 5 tail buckets = 10 patterns.
        expected = len(roots) * 10
        assert before >= expected


class TestSearchGuards:
    def test_disallowed_root_refused(self):
        result = rs.remote_search("admin", "/etc", "")
        assert result["success"] is False
        assert result["error_code"] == "not_allowed"

    def test_unsafe_term_refused(self):
        result = rs.remote_search("a; rm -rf /", "/var/www", "")
        assert result["success"] is False
        assert result["error_code"] == "invalid_input"

    def test_traversal_refused(self):
        result = rs.remote_search("x", "/var/www", "../../../etc")
        assert result["success"] is False
        assert result["error_code"] == "not_allowed"

    def test_secret_files_refused(self):
        result = rs.remote_tail("/var/www", ".env", 10)
        assert result["success"] is False
        assert result["error_code"] == "not_allowed"
        result = rs.remote_tail("/var/www", "app/id_rsa", 10)
        assert result["success"] is False


class TestExecution:
    def test_search_executes_via_executor(self, monkeypatch):
        captured = {}

        def fake_run(command):
            captured["command"] = command
            return {"success": True, "stdout": "file.py:1:password=secret\nfile.py:2:ok\n", "stderr": "", "exit_code": 0}

        monkeypatch.setattr(rs, "_run", fake_run)
        result = rs.remote_search("admin", "/var/www", "app")
        assert result["success"] is True
        assert "password=secret" not in "\n".join(result["lines"])
        assert "ok" in "\n".join(result["lines"])
        # Command must be whitelist-acceptable in shape (literal root baked in).
        assert captured["command"].startswith("grep -rIn -m 50 -e admin /var/www/app")

    def test_match_bound_enforced(self, monkeypatch):
        monkeypatch.setattr(rs, "_run", lambda command: {"success": True, "stdout": "\n".join(f"line{i}" for i in range(500)), "stderr": "", "exit_code": 0})
        result = rs.remote_search("admin", "/var/www", "")
        assert len(result["lines"]) <= rs.MAX_MATCHES
        assert result["truncated"] is True

    def test_tail_bounded_lines(self, monkeypatch):
        captured = {}

        def fake_run(command):
            captured["command"] = command
            return {"success": True, "stdout": "log", "stderr": "", "exit_code": 0}

        monkeypatch.setattr(rs, "_run", fake_run)
        rs.remote_tail("/var/www", "logs/app.log", 99999)
        assert "tail -n 500" in captured["command"]

    def test_list_depth_bounded(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(rs, "_run", lambda command: captured.update(command=command) or {"success": True, "stdout": "", "stderr": "", "exit_code": 0})
        rs.remote_list("/var/www", "", 99)
        assert "-maxdepth 4" in captured["command"]
