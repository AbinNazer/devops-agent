"""
Central config loader. Reads from environment variables (populated from
.env in local dev via python-dotenv). No secrets ever hardcoded here.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; harmless if it isn't


class Config:
    # Future SaaS database. The current application does not connect to this
    # value yet; it is intentionally configuration-only until migrations are
    # reviewed and a PostgreSQL adapter is implemented.
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    DATABASE_ENABLED = os.environ.get("DATABASE_ENABLED", "false").lower() == "true"
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
    # Empty by default — deliberately NOT crashing at import time (this class
    # is imported by every module and CI, which never has these set). Instead,
    # app/auth.py.authenticate() refuses ALL logins when either is empty.
    WEB_USERNAME = os.environ.get("JARVIS_WEB_USERNAME", "")
    WEB_PASSWORD = os.environ.get("JARVIS_WEB_PASSWORD", "")
    COOKIE_SECURE = os.environ.get("JARVIS_COOKIE_SECURE", "false").lower() == "true"
    EMAIL_VERIFICATION_REQUIRED = os.environ.get("JARVIS_EMAIL_VERIFICATION_REQUIRED", "false").lower() == "true"
    ALLOWED_ORIGINS = [item.strip() for item in os.environ.get("JARVIS_ALLOWED_ORIGINS", "http://127.0.0.1:8001,http://localhost:8001").split(",") if item.strip()]
    LOGIN_RATE_LIMIT = int(os.environ.get("JARVIS_LOGIN_RATE_LIMIT", "10"))
    LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get("JARVIS_LOGIN_RATE_WINDOW_SECONDS", "300"))
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

    # Execution mode: "ssh" (laptop -> VPS) or "local" (on VPS directly)
    EXECUTION_MODE = os.environ.get("EXECUTION_MODE", "ssh")

    # Jenkins (runs as a Docker container on the VPS)
    JENKINS_CONTAINER_NAME = os.environ.get("JENKINS_CONTAINER_NAME", "jenkins")

    # AWS (read-only EC2/CloudWatch monitoring). Credentials themselves are
    # NOT read here — boto3 resolves them itself via env vars, ~/.aws/credentials,
    # or an instance role. This is only the region, since boto3 needs one.
    AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", ""))

    # Prometheus (Phase 6 — accessed via SSH tunnel to VPS localhost)
    PROMETHEUS_PORT = (
        int(os.environ["PROMETHEUS_PORT"])
        if os.environ.get("PROMETHEUS_PORT")
        else None
    )

    # Google Gemini (hosted)
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # OpenAI (hosted)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
    OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")  # e.g. AgentRouter

    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")

    NVIDIA_NIM_API_KEY = os.environ.get("NVIDIA_NIM_API_KEY", "")
    NVIDIA_NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "meta/llama-3.1-70b-instruct")

    # Anthropic (hosted)
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    # Multi-provider failover: comma-separated list of fallback providers.
    # The primary provider is always LLM_PROVIDER. Fallbacks are tried in
    # order when the primary (or a previous fallback) fails with a retryable
    # error (rate limit, timeout, 5xx, model unavailable).
    # Optional local voice providers. Text chat is unaffected when disabled.
    VOICE_ENABLED = os.environ.get("VOICE_ENABLED", "false").lower() == "true"
    STT_PROVIDER = os.environ.get("STT_PROVIDER", "whisper")
    TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "kokoro")
    KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "am_michael")
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")

    # Worker/control-plane settings. The control plane currently uses these
    # as development configuration; worker credentials are never exposed to UI.
    WORKER_ENABLED = os.environ.get("WORKER_ENABLED", "true").lower() == "true"
    WORKER_CONTROL_PLANE_URL = os.environ.get("WORKER_CONTROL_PLANE_URL", "http://127.0.0.1:8001")
    WORKER_ID = os.environ.get("WORKER_ID", "")
    WORKER_ENROLLMENT_TOKEN = os.environ.get("WORKER_ENROLLMENT_TOKEN", "")
    LLM_FALLBACK_PROVIDERS = [
        p.strip() for p in os.environ.get("LLM_FALLBACK_PROVIDERS", "").split(",") if p.strip()
    ]


def validate_vps_config() -> list:
    """
    Check VPS config at startup instead of only discovering problems on the
    first tool call. Returns a list of human-readable problems — empty
    means everything looks fine. Doesn't check network reachability (that's
    what check_network_connectivity does); just catches config mistakes.
    """
    problems = []
    if Config.EXECUTION_MODE.lower() == "local":
        return []
    if not Config.VPS_HOST:
        problems.append("VPS_HOST is not set in .env")
    if not Config.VPS_SSH_USER:
        problems.append("VPS_SSH_USER is not set in .env")
    if Config.VPS_SSH_KEY_PATH and not os.path.exists(Config.VPS_SSH_KEY_PATH):
        problems.append(f"SSH key not found at {Config.VPS_SSH_KEY_PATH} (check VPS_SSH_KEY_PATH in .env)")
    return problems
