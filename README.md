# JARVIS — AI DevOps Agent

JARVIS is a security-controlled DevOps assistant. It can inspect local and remote infrastructure, preserve operational memory, reason over evidence, propose controlled actions, and use multiple LLM providers without giving any model direct shell or SSH access.

> Development foundation, not production SaaS. Do not expose the API publicly or use this as a replacement for reviewed operational procedures.

## Current project status

JARVIS is currently a single-installation DevOps agent with a responsive web
UI, PWA support, provider selection, SSE chat, infrastructure status, and a
separate WebSocket/PTy terminal. The SaaS work is being added incrementally.

Completed foundations:

- Architecture documentation in [`docs/current-architecture.md`](docs/current-architecture.md).
- Authenticated session identity and Owner/Admin/Engineer/Viewer RBAC contracts.
- Non-persistent user, organization, settings, and infrastructure repository
  interfaces with ownership checks.
- Reviewable PostgreSQL schema draft in
  [`db/migrations/001_saas_foundation.sql`](db/migrations/001_saas_foundation.sql).
- Authenticated user/organization Settings API and current Settings UI.
- Optional PostgreSQL-backed web sessions with expiry.
- Password registration using server-side `scrypt` hashes.
- Encrypted secret-storage primitive using a server-side Fernet key.
- Provider/model catalog and provider preference APIs.
- Organization-owned PostgreSQL conversation repository and compatibility
  cutover support when `DATABASE_ENABLED=true`.

The PostgreSQL migration is not run automatically. The existing `memory.db`
and web conversation files under `sessions/` are separate and must not be
deleted, reset, or migrated as part of normal startup.

Google OAuth is not implemented yet. The current account flow supports the
configured server account and local PostgreSQL-backed username/password
registration when database mode is enabled. A complete provider-management
form and full RBAC enforcement across every legacy route are also still under
development.

### Optional PostgreSQL persistence

PostgreSQL is separate from `memory.db` and is currently opt-in. Install the
dependency, set a private connection URL, review the migration, and apply it
explicitly:

```bash
pip install -r requirements.txt
export DATABASE_URL='postgresql://user:password@host:5432/jarvis'
export ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export DATABASE_ENABLED=true
python scripts/migrate_postgres.py --database-url "$DATABASE_URL"
```

The application does not connect or migrate automatically. Never commit the
URL, and do not run the migration against production without backups and a
rollback plan.

`ENCRYPTION_KEY` is required for encrypted secrets and must be backed up
securely outside the repository. Losing it makes encrypted provider
credentials unrecoverable. The current PostgreSQL session store is enabled
only when `DATABASE_ENABLED=true`; otherwise the existing in-memory session
fallback remains active.

When `DATABASE_ENABLED=true`, apply the migration before starting the server.
This enables PostgreSQL-backed sessions and new organization-owned
conversation persistence. Existing JSON conversations are not automatically
migrated or deleted. When it is `false`, the legacy JSON conversation store
and in-memory session fallback remain active.

## Architecture

```text
Responsive web/PWA UI
  Chat, conversations, providers, settings, status, terminal
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
| `app/static/` | Responsive web/PWA UI, chat, settings, status, and standalone terminal client. |
| `app/api/` | REST/SSE API for chat, providers, conversations, settings, voice, tasks, and control-plane endpoints. |
| `app/agent.py`, `app/control/` | Reasoning and controlled operation lifecycle. |
| `app/memory/`, `app/intelligence/` | Persistent local memory, retrieval, incident analysis, and recommendations. |
| `app/llm_router.py` | Provider selection, context preservation, cooldown, and safe failover. |
| `app/tools/`, `app/tool_registry.py` | Explicit, validated infrastructure operations. |
| `app/ssh_whitelist.py` | Strict allowed-command validation for SSH access. |
| `app/control_plane/` | Existing control-plane services for organization/project/infrastructure/worker workflows. |
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

The web API is served directly by FastAPI; the current UI is static and does
not require a separate frontend build or Node.js installation.

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

### Web/PWA UI

Open `http://127.0.0.1:8001/` after starting the API. On a phone, use the
browser's Add to Home Screen action. The terminal opens as a separate
full-screen page from the application menu.

The UI includes responsive two-sided chat bubbles, streaming responses, an
animated assistant avatar, provider/settings controls, Aurora Dark and Bright
Light themes, infrastructure status, logout, and the local terminal view.
Static UI changes do not require a database migration.

### Updating a VPS UI-only deployment

When PostgreSQL is disabled (`DATABASE_ENABLED=false`), pull UI updates and
restart the service:

```bash
cd /var/www/k3s/devops-agent
git pull
sudo systemctl restart devops-agent
```

No migration is needed for CSS, JavaScript, HTML, or PWA-cache changes. If a
phone home-screen app still shows an older version, close it completely and
open it again; the static asset cache version is bumped with UI releases.

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

Before any public or customer-facing deployment, complete persistent PostgreSQL-backed users/RBAC, Redis-backed dispatch where needed, TLS/mTLS, secret manager/KMS, token rotation, rate limiting, audit retention, backup/recovery, worker sandboxing, signed updates, centralized observability, and a reviewed deployment model.

## Tool Factory (read-only generated tools)

The Tool Factory (`app/tool_factory/`) is a controlled pipeline for creating, versioning, and executing **read-only** DevOps tools from structured definitions. It does not replace the existing tool registry, the SSH whitelist, or the Phase 5 control pipeline — it plugs into them.

### Security model

- Generated tools are **read-only by default** and can never execute arbitrary shell.
- Command templates may not contain shell metacharacters (`;`, `|`, `&`, `$`, backticks, redirects) or dangerous binaries (`rm`, `docker exec/rm/kill/system prune`, `kubectl delete/apply/exec`, `systemctl stop`, `sudo`, `curl|bash`, `wget|bash`, package installers, etc.).
- Every execution passes: RBAC permission check → input JSON-schema validation → safe placeholder substitution → **SSH whitelist final gate** → unified executor (local or SSH mode) → timeout, output truncation, and secret redaction → audit record.
- Secrets in output are redacted (values removed, key names preserved); `.env` and credential files are never returned by remote search.
- Mutating operations (container start/stop/restart) remain outside the Tool Factory and inside the existing Phase 5 policy → risk → approval → execution → verification flow.
- Lifecycle is governance-gated: definitions must be validated and approved before activation; only one version is active at a time; activate/deactivate/rollback are audited and require the `tools.manage` permission (admins).

### Remote search

`app/tool_factory/remote_search.py` provides bounded, redacted search over **server-configured roots only** (`JARVIS_SEARCH_ALLOWED_ROOTS`, comma-separated absolute paths; traversal segments are refused). Supported read-only commands (grep with max matches, find at bounded depth, tail with bounded lines) are registered into the SSH whitelist at startup from server config — the browser/LLM can only pick a root and fill safe slots, never supply raw commands.

### Project intelligence correlation

`app/project_intelligence.py` gained a correlation pass that links error messages with container names, Compose services, project folders, and dependency/config files, returning confidence and evidence paths. Current live evidence always outranks historical memory.

### Research progress & memory

`app/research.py` tracks learned skills (topic, detected version, source, confidence, safety notes, refresh timestamp), supports refresh and version comparison, reports source disagreement, and persists learned skills into Phase 4 memory. Research findings are advisory only and never executed automatically.

### API and UI

New authenticated endpoints (see `/docs`): `/api/tools` (list/create/validate/activate/deactivate/rollback/execute/audit/versions) plus `/api/project/context`, `/api/project/scan`, and `/api/research/{refresh,progress,disagreements}`. Write operations require admin/`tools.manage`; viewers are read-only. The frontend "Tools & Automation" settings section exposes the same capabilities.

### Configuration

```
JARVIS_SEARCH_ALLOWED_ROOTS=/var/www,/etc/nginx
```

Optional; empty means remote search is disabled. Tool Factory storage follows the existing database opt-in: `DATABASE_ENABLED=true` + `DATABASE_URL` use PostgreSQL (migration `db/migrations/002_tool_factory.sql`); otherwise an in-memory repository is used.
