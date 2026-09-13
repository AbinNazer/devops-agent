# JARVIS — AI DevOps Agent

JARVIS is a security-controlled DevOps assistant. It can inspect local and remote infrastructure, preserve operational memory, reason over evidence, propose controlled actions, and use multiple LLM providers without giving any model direct shell or SSH access.

> Development foundation, not production SaaS. Do not expose the API publicly or use this as a replacement for reviewed operational procedures.

## Architecture

```text
Desktop UI (React/Tauri)
  Chat, conversations, providers, tasks, infrastructure, voice
                         │ HTTP / SSE
                         ▼
FastAPI API / Control Plane
  Conversation API · provider API · task API · worker enrollment
                         │
                         ▼
Nucleus
  Agent · memory · intelligence · planning · verification
                         │
                         ▼
LLM Router ─── Ollama / Groq / OpenAI / Anthropic
                         │
                         ▼
Security boundary
  Tool registry · policy firewall · risk · approval · audit · SSH whitelist
                         │
               ┌─────────┴─────────┐
               ▼                   ▼
       Direct local/VPS tools   Outbound worker foundation
               │                   │
        Docker, AWS, K3s,       explicit capabilities only
        Prometheus, Linux       no arbitrary shell
```

### Responsibilities

| Layer | Responsibility |
|---|---|
| `desktop/` | ChatGPT-style desktop UI and frontend state/services. |
| `app/api/` | REST/SSE API for chat, providers, conversations, voice, tasks, and control-plane endpoints. |
| `app/agent.py`, `app/control/` | Reasoning and controlled operation lifecycle. |
| `app/memory/`, `app/intelligence/` | Persistent local memory, retrieval, incident analysis, and recommendations. |
| `app/llm_router.py` | Provider selection, context preservation, cooldown, and safe failover. |
| `app/tools/`, `app/tool_registry.py` | Explicit, validated infrastructure operations. |
| `app/ssh_whitelist.py` | Strict allowed-command validation for SSH access. |
| `app/control_plane/` | Organizations, projects, infrastructure, workers, enrollment, and heartbeat ownership. |
| `worker/` | Capability declarations and structured task protocol for a future outbound worker. |

## Security model

- The LLM requests tools as structured data; it never executes commands itself.
- SSH commands must pass the whitelist, policy firewall, risk assessment, and approval flow.
- Destructive and arbitrary shell operations remain blocked.
- SSH host keys are verified; unknown hosts are rejected.
- Secrets are redacted before memory/audit storage where supported.
- Provider switching never changes tool permissions, target authorization, risk, approval, or audit identity.
- Worker enrollment tokens are organization/project/infrastructure scoped, expire, are single use, and are stored only as hashes.
- Worker tokens are stored as hashes and are sent only by the worker in the `X-Worker-Token` request header.

## Requirements

- Python 3.11+ (the project currently uses Python 3.12)
- Node.js 20+ and npm for the desktop UI
- Git
- Optional: Ollama, Docker, a Linux VPS, AWS credentials, and Prometheus

On Ubuntu/WSL, install basic prerequisites:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm git
```

## First-time setup

```bash
git clone <your-repository-url> devops-agent
cd devops-agent
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

The web API requires FastAPI and Uvicorn. If they are not already installed by your dependency file, install them into the same virtual environment:

```bash
pip install fastapi 'uvicorn[standard]'
```

Install desktop dependencies:

```bash
cd desktop
npm install
cd ..
```

## Environment configuration

Create `.env` in the repository root. Never commit it and never paste its contents into chat, issues, or frontend code.

Start with the smallest configuration that matches your use case. You do **not** need every provider or service.

```env
# ------------------------------------------------------------
# LLM ROUTING — choose one primary provider
# ------------------------------------------------------------
LLM_PROVIDER=groq
LLM_FALLBACK_PROVIDERS=openai,ollama

# Groq (recommended low-RAM starting point)
GROQ_API_KEY=replace_with_your_groq_key
GROQ_MODEL=llama-3.3-70b-versatile

# OpenAI — optional fallback
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o

# Anthropic — optional fallback
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# Ollama — optional local model provider
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# ------------------------------------------------------------
# VPS / SSH — leave blank if you use local tools only
# ------------------------------------------------------------
VPS_HOST=
VPS_SSH_USER=
VPS_SSH_KEY_PATH=~/.ssh/id_ed25519
VPS_SSH_PORT=22
VPS_KNOWN_HOSTS_PATH=~/.ssh/known_hosts
JENKINS_CONTAINER_NAME=jenkins

# ------------------------------------------------------------
# AWS — credentials are resolved by boto3; do not paste them here
# ------------------------------------------------------------
AWS_DEFAULT_REGION=ap-south-1

# ------------------------------------------------------------
# Monitoring — Prometheus is reached through the existing SSH tunnel
# ------------------------------------------------------------
PROMETHEUS_PORT=4001

# ------------------------------------------------------------
# Worker / control-plane development
# ------------------------------------------------------------
# Generate a long random value, never use the example literal.
WORKER_TOKEN_HASH_SECRET=replace_with_a_long_random_value
```

Generate the worker token hashing secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Provider choices

| Scenario | Recommended values |
|---|---|
| Small 8 GB machine | `LLM_PROVIDER=groq`; set only `GROQ_API_KEY`. |
| Fully local model | `LLM_PROVIDER=ollama`; install/start Ollama and pull the configured model. |
| Hosted fallback | Set `LLM_FALLBACK_PROVIDERS=openai` and an OpenAI key. |
| No fallback | Leave `LLM_FALLBACK_PROVIDERS` empty. |

Provider credentials remain backend-only. `/api/providers` returns safe status and model metadata, never API keys.

## Run JARVIS

Activate the environment in every new terminal:

```bash
cd ~/ai/devops-agent
source .venv/bin/activate
```

### CLI agent

```bash
python -m app.main
```

### API backend

```bash
uvicorn app.api.app:app --host 127.0.0.1 --port 8001 --reload
```

Open API documentation at `http://127.0.0.1:8001/docs` while the backend is running. Keep `127.0.0.1` for development; do not bind publicly without authentication, TLS, and a reverse proxy.

### Desktop UI

In a second terminal:

```bash
cd ~/ai/devops-agent/desktop
npm run dev
```

For a production-style frontend build check:

```bash
npm run build
```

## Configure infrastructure

### Local tools

No SSH configuration is required. Start the backend and ask read-only questions such as:

```text
Check local CPU, memory, and disk.
List my Docker containers.
```

JARVIS only exposes tools registered in `app/tool_registry.py`; it does not accept arbitrary commands.

### VPS through SSH

1. Create an SSH key pair if necessary: `ssh-keygen -t ed25519`.
2. Add the public key to the intended VPS user's `~/.ssh/authorized_keys`.
3. Connect once manually to verify the server host key:

```bash
ssh -i ~/.ssh/id_ed25519 your-user@your-vps-host
```

4. Set `VPS_HOST`, `VPS_SSH_USER`, and `VPS_SSH_KEY_PATH` in `.env`.
5. Restart JARVIS and use read-only checks first.

Do not disable host-key checking and do not broaden the command whitelist merely to make a request work.

### AWS

Use the standard AWS credential chain instead of passing credentials to JARVIS:

```bash
aws configure
# or use an IAM role / AWS_PROFILE in your shell
```

Set `AWS_DEFAULT_REGION` in `.env`. Start with the existing read-only operations.

### Prometheus

JARVIS expects Prometheus to be reachable on the VPS loopback interface through the existing SSH connection. Set:

```env
PROMETHEUS_PORT=4001
```

The tunnel binds locally only and monitoring remains read-only. Do not publish Prometheus merely for JARVIS.

## Control plane and worker setup

The control plane is currently an in-memory development foundation. Restarting the API clears organizations, projects, infrastructure records, workers, and enrollment tokens. MySQL, Redis, and Qdrant are deliberately not mandatory yet.

Create resources in this order:

```text
Organization → Project → Infrastructure → Enrollment token → Worker → Heartbeat
```

Example using the FastAPI docs or `curl`:

```bash
# Create organization
curl -X POST http://127.0.0.1:8001/api/control/organizations \
  -H 'Content-Type: application/json' \
  -d '{"name":"My Organization"}'

# Create project (replace org_...)
curl -X POST http://127.0.0.1:8001/api/control/projects \
  -H 'Content-Type: application/json' \
  -d '{"organization_id":"org_...","name":"Home Lab"}'

# Create infrastructure (replace IDs)
curl -X POST http://127.0.0.1:8001/api/control/infrastructure \
  -H 'Content-Type: application/json' \
  -d '{"organization_id":"org_...","project_id":"project_...","name":"Local Docker","type":"local"}'

# Create short-lived, single-use enrollment token
curl -X POST http://127.0.0.1:8001/api/control/enrollments \
  -H 'Content-Type: application/json' \
  -d '{"organization_id":"org_...","project_id":"project_...","infrastructure_id":"infra_...","ttl_minutes":15}'
```

Use the enrollment token once with `POST /api/workers/register`. Store the returned `worker_token` only on the worker machine; it is required for `POST /api/workers/{worker_id}/heartbeat` as `X-Worker-Token`.

The worker package currently defines a strict capability catalog and message protocol. It is not yet a deployed daemon or distributed task runner. Do not treat it as a production agent installer.

## Memory, tasks, and conversation behavior

- Conversations are persisted under `sessions/`.
- Local memory is stored in `memory.db`.
- Changing LLM providers does not create a new conversation; previous messages and tool data remain in the context passed to the next provider.
- Long-running task cancellation is task-specific. Cancellation does not cancel other conversations or tasks.
- Voice is optional. Text chat works without Whisper or Kokoro installed.

## Testing

Run unit tests:

```bash
source .venv/bin/activate
pytest -q
```

Run the focused control-plane and router tests:

```bash
pytest -q tests/test_control_plane.py tests/test_llm_router.py tests/test_model_unavailable.py
```

Build the desktop application:

```bash
cd desktop
npm run build
```

Optional tests that contact real infrastructure/providers are opt-in and may consume quota or connect to your VPS:

```bash
RUN_LLM_INTEGRATION_TESTS=1 pytest -v
RUN_VPS_INTEGRATION_TESTS=1 pytest -v
```

## Project layout

```text
app/
  api/                 FastAPI endpoints, chat/task/voice state
  control/             policy, risk, approval, verification, rollback
  control_plane/       org/project/infrastructure/worker coordination
  intelligence/        diagnostics and recommendations
  memory/              SQLite-backed memory and retrieval
  monitoring/          bounded monitoring and Prometheus support
  tools/               local, VPS, Docker, AWS, Jenkins, K3s tools
  llm_provider.py      individual provider clients
  llm_router.py        provider routing and failover
  ssh_whitelist.py     SSH command validation
worker/                future outbound worker protocol/capabilities
desktop/               React/Tauri desktop UI
sessions/              persisted chat sessions
logs/                  application and audit logs
tests/                 automated tests
```

## 8 GB RAM development plan

Start with only:

```text
Desktop UI + backend + one hosted LLM provider
```

Then add services only when working on their feature:

1. Ollama for local-model testing.
2. Qdrant for vector retrieval.
3. MySQL for durable control-plane state.
4. Redis for distributed task dispatch/cancellation.
5. Whisper/Kokoro for voice.

Do not run Ollama, Qdrant, MySQL, Redis, Whisper, Kokoro, Docker/K3s, and a large local model together on 8 GB RAM.

## Future production hardening

Before any public or customer-facing deployment, add authenticated users/RBAC, MySQL persistence, Redis-backed dispatch, TLS/mTLS, secret manager/KMS, token rotation, rate limiting, audit retention, backup/recovery, worker sandboxing, signed updates, centralized observability, and a reviewed deployment model.