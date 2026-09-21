"""Whitelist registration boundary tests (server-config-derived patterns)."""
import pytest

from app.ssh_whitelist import register_readonly_pattern, is_command_allowed


class TestRegisterReadonlyPattern:
    def test_grep_pattern_accepted_and_enforced(self):
        assert register_readonly_pattern("grep -rIn -m 50 -e {query} /var/www") is True
        assert is_command_allowed("grep -rIn -m 50 -e admin /var/www")
        # Unsafe query text can never match the strict slot regex.
        assert not is_command_allowed("grep -rIn -m 50 -e 'a; rm' /var/www")

    def test_mutation_binary_rejected(self):
        assert register_readonly_pattern("rm -rf {path}") is False
        assert register_readonly_pattern("docker exec {c} sh") is False
        assert register_readonly_pattern("sudo grep x /var/www") is False

    def test_metacharacters_rejected(self):
        assert register_readonly_pattern("grep x /var/www ; rm -rf /") is False
        assert register_readonly_pattern("cat /etc/passwd | wc -l") is False
        assert register_readonly_pattern("grep $(x) /var/www") is False

    def test_traversal_path_rejected(self):
        assert register_readonly_pattern("grep x /var/../etc") is False

    def test_empty_and_non_string_rejected(self):
        assert register_readonly_pattern("") is False
        assert register_readonly_pattern(None) is False

    def test_duplicate_registration_is_stable(self):
        pattern = "grep -rIn -m 50 -e {query} /opt/app"
        before = is_command_allowed("grep -rIn -m 50 -e q /opt/app")
        assert register_readonly_pattern(pattern) is True
        assert register_readonly_pattern(pattern) is True  # idempotent
        assert is_command_allowed("grep -rIn -m 50 -e q /opt/app") or before

    def test_builtin_whitelist_unchanged(self):
        # The original fail-closed surface keeps working regardless of registrations.
        assert is_command_allowed("df -h")
        assert is_command_allowed("docker ps -a")
        assert not is_command_allowed("rm -rf /")
        assert not is_command_allowed("docker exec x sh")
