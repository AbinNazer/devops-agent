"""
Central config loader. Reads from environment variables (populated from
.env in local dev via python-dotenv). No secrets ever hardcoded here.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; harmless if it isn't


class Config:
    # Which LLM backend to use: "ollama" or "groq"
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")

    # Ollama (local)
    OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

    # Groq (hosted)
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    # VPS SSH access (Phase 2 — read-only)
    VPS_HOST = os.environ.get("VPS_HOST", "")
    VPS_SSH_USER = os.environ.get("VPS_SSH_USER", "")
    VPS_SSH_KEY_PATH = os.path.expanduser(os.environ.get("VPS_SSH_KEY_PATH", "~/.ssh/id_ed25519"))
    VPS_SSH_PORT = int(os.environ.get("VPS_SSH_PORT", "22"))
    VPS_KNOWN_HOSTS_PATH = os.path.expanduser(os.environ.get("VPS_KNOWN_HOSTS_PATH", "~/.ssh/known_hosts"))

    # Jenkins (runs as a Docker container on the VPS)
    JENKINS_CONTAINER_NAME = os.environ.get("JENKINS_CONTAINER_NAME", "jenkins")

    # AWS (read-only EC2/CloudWatch monitoring). Credentials themselves are
    # NOT read here — boto3 resolves them itself via env vars, ~/.aws/credentials,
    # or an instance role. This is only the region, since boto3 needs one.
    AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", ""))


def validate_vps_config() -> list:
    """
    Check VPS config at startup instead of only discovering problems on the
    first tool call. Returns a list of human-readable problems — empty
    means everything looks fine. Doesn't check network reachability (that's
    what check_network_connectivity does); just catches config mistakes.
    """
    problems = []
    if not Config.VPS_HOST:
        problems.append("VPS_HOST is not set in .env")
    if not Config.VPS_SSH_USER:
        problems.append("VPS_SSH_USER is not set in .env")
    if Config.VPS_SSH_KEY_PATH and not os.path.exists(Config.VPS_SSH_KEY_PATH):
        problems.append(f"SSH key not found at {Config.VPS_SSH_KEY_PATH} (check VPS_SSH_KEY_PATH in .env)")
    return problems