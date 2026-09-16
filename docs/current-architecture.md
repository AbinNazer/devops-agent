# JARVIS Current Architecture

This document records the repository as inspected on 2026-09-15. It describes
what exists today; it is not a claim that the future SaaS architecture is
already implemented.

## Runtime entry points

- CLI/runtime entry point: `app/main.py`.
- Web entry point: `app.api.app:app`, served with Uvicorn.
- Optional worker entry point: `worker/main.py`.
- Browser application: `app/static/index.html` and `app/static/app.js`.
- Standalone terminal: `app/static/terminal.html` and `app/static/terminal.js`.

The FastAPI application serves the static UI, chat REST/SSE endpoints,
provider listing/switching, health and infrastructure status endpoints, task
endpoints, voice endpoints, authentication endpoints, and the terminal router.

## Current request flow

```text
Browser
  -> FastAPI app
      -> conversation store (JSON files under sessions/)
      -> LLM router / provider
          -> Agent
              -> tool registry
                  -> local, Docker, K3s, Jenkins, AWS, network, process,
                     server and monitoring tools
      -> SSE response stream
```

The existing agent remains the orchestration point for chat/tool execution.
The UI does not expose provider keys or SSH keys.

## Existing modules

### Agent and tools

- `app/agent.py`: current agent and system prompt.
- `app/tool_registry.py`: tool schemas, tool names, execution dispatch, and
  memory command handling.
- `app/tools/`: infrastructure integrations including local host, Docker,
  K3s, Jenkins, AWS, networking, processes, health, and server operations.
- `app/executor.py`, `app/control/`, and `app/control_plane/`: policy,
  planning, risk, verification, rollback, incident/control-plane behavior.

These modules are existing production behavior and are intentionally not
changed by the Phase 1 documentation work.

### Model providers

- `app/llm_provider.py`: provider implementations.
- `app/llm_router.py`: provider states, active provider, fallback behavior,
  and provider construction.
- `app/config.py`: environment-backed platform configuration and provider
  model names/API keys.

The current configuration supports Ollama, Groq, Gemini, OpenAI, OpenRouter,
NVIDIA NIM, and Anthropic settings. User-owned encrypted provider credentials
and a model registry do not exist yet.

### Memory and sessions

- `app/memory/`: repository, store, retrieval, scoring, consolidation,
  preferences, relationships, reflection, feedback, and memory commands.
- `memory.db`: existing memory data; must remain untouched.
- `app/session.py`: existing session functionality.
- `app/api/conversation_store.py`: web conversations persisted as JSON files
  in `sessions/web_<conversation-id>.json`.

The web conversation store is not tenant-aware today. It has no organization
or user ownership boundary.

### Monitoring and intelligence

- `app/intelligence/`: analysis, diagnosis, correlation, health, incidents,
  recommendations, risk, and summaries.
- `app/monitoring/`: collectors, Prometheus integration, anomaly detection,
  cooldown, deduplication, incidents, thresholds, scheduler, and SSH tunnel.

These are reusable foundations for a future Nucleus/incident subsystem, but
there is no SaaS job queue or organization-scoped incident store yet.

### SSH and terminal

- `app/ssh_client.py`, `app/ssh_session_manager.py`, and
  `app/ssh_whitelist.py`: existing SSH behavior and restrictions.
- `app/terminal/`: terminal manager, PTY session, and WebSocket/API routes.
- `worker/`: capability registry, protocol, and worker entry point.

The browser terminal uses WebSocket + PTY. Deployment mode is configured by
`EXECUTION_MODE`; the existing local mode must remain local when JARVIS runs
on the target VPS. A future worker gateway can build on `worker/`, but a
multi-organization authenticated worker protocol is not complete yet.

## Authentication and security boundary

The current web UI has username/password login configured by
`JARVIS_WEB_USERNAME` and `JARVIS_WEB_PASSWORD`. A server-side in-memory
session token is stored in an HTTP-only cookie. The current implementation is
single-instance oriented: sessions are lost when the process restarts, and
most API resources do not yet have tenant/RBAC ownership checks.

Provider and SSH secrets are read server-side from environment/configuration;
they are not sent to the browser. Encrypted customer credential storage,
password hashing, refresh tokens, RBAC, audit logs, rate limiting, and
production WebSocket authorization remain future work.

Phase 2 now adds `app/tenancy.py` with explicit `Organization`, `Membership`,
`TenantContext`, `Role`, and permission contracts. Authenticated sessions map
to a stable user ID and the default organization; `/api/auth/me` exposes only
non-secret identity, organization, role, and permission metadata. Existing
conversation, tool, provider, and terminal routes are intentionally not
retroactively restricted until persistent ownership storage and migration
rules are designed.

## Frontend

The existing frontend is a dependency-light static application:

- `index.html`: login, chat, provider menu, status view, PWA metadata.
- `app.css`: current responsive desktop/mobile visual system.
- `app.js`: login, conversation creation/persistence, provider switching,
  SSE chat, Markdown/card rendering, status view, and navigation.
- `terminal.html`/`terminal.js`: separate full-screen xterm.js terminal.
- `manifest.webmanifest`, `sw.js`, and SVG icons: PWA support.

The current UI width, mobile behavior, standalone terminal, and PWA approach
are intentionally preserved. There is no frontend build framework or separate
`frontend/` directory in this repository.

## Data and persistence inventory

| Data | Current location | Phase 1 treatment |
| --- | --- | --- |
| Agent memory | `memory.db` and memory modules | Preserve; no migration |
| Web conversations | `sessions/web_*.json` | Preserve; no tenant migration |
| Server configuration/secrets | `.env` / process environment | Never commit or expose |
| Provider/router state | Process memory and configuration | Preserve existing behavior |
| Terminal sessions | Process/runtime state | Preserve existing terminal flow |

No database was created, dropped, migrated, or rewritten during Phase 1.

## SaaS readiness gaps

The smallest safe future phases are:

1. Introduce interfaces for settings, secret storage, and resource ownership
   without changing existing storage.
2. Add a non-destructive PostgreSQL schema/migration project separately from
   `memory.db` and web JSON sessions.
3. Add authenticated user/organization context and backend authorization
   before exposing organization-owned resources.
4. Add provider credential storage behind a server-side encryption/SecretStore
   abstraction; never send raw keys to the browser.
5. Add infrastructure and worker records with explicit organization ownership.
6. Add Settings UI only after its backend contracts are real.

No feature should be advertised as multi-tenant until backend isolation and
tests exist.

Phase 3 adds `app/repositories.py` with PostgreSQL-ready contracts and a
non-persistent `InMemorySaaSRepository` adapter for users, organizations,
settings, and infrastructure records. Infrastructure reads require an
organization ID and reject cross-organization access. This adapter is not
connected to existing chat sessions or memory storage yet.

Phase 4 adds a reviewable PostgreSQL schema draft at
`db/migrations/001_saas_foundation.sql`, covering users, organizations,
memberships, settings, and infrastructure. `DATABASE_URL` is configuration-
only at this stage. Application startup does not connect to PostgreSQL and no
migration runs automatically.

Production-session and secret-storage foundations are now available. When
`DATABASE_ENABLED=true`, web sessions are stored in PostgreSQL as hashed
tokens with expiry; the raw cookie token is never stored in the database.
`app/secret_store.py` encrypts secrets with a Fernet key from `ENCRYPTION_KEY`
and stores only ciphertext. Both features remain opt-in until the migration
is applied and the key is backed up securely.

PostgreSQL persistence now has an explicit opt-in adapter in
`app/postgres.py` and migration command:

```bash
python scripts/migrate_postgres.py --database-url "$DATABASE_URL"
```

The command is never run by application startup. Review the SQL, backups,
access controls, and rollback plan before using it against any environment.

Phase B adds PostgreSQL-owned `conversations` and `conversation_messages`
tables plus `app/conversation_repository.py`. Every record carries both
organization and user ownership, and message writes verify ownership before
inserting. Existing JSON conversations are intentionally not migrated or
deleted; API route cutover remains a separate tested step.

The repository now supports owned history reads, message writes, rename,
provider updates, and deletion. The existing API still uses the JSON adapter
until an end-to-end SSE/API cutover test is added.

## Verification baseline

The repository contains a broad pytest suite under `tests/`. The required
baseline command is:

```bash
python -m compileall -q app worker tests
python -m pytest -q
```

Phase 1 documentation itself does not alter runtime code or data. Test
execution should be performed in the project virtual environment where all
declared dependencies are installed.
