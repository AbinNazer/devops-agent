# Control-plane and worker development foundation

JARVIS keeps the LLM, security controls, tool registry, and SSH whitelist unchanged. The new `app.control_plane` package is the coordination boundary for organizations, projects, infrastructure, workers, and enrollment.

## Local flow

1. Create an organization, project, and infrastructure through `/api/control/*`.
2. Create a short-lived enrollment token with `/api/control/enrollments`.
3. The worker registers through `/api/workers/register`; store its returned worker token locally.
4. Send a heartbeat to `/api/workers/{id}/heartbeat` with `X-Worker-Token`.

Enrollment tokens are organization/project/infrastructure scoped, expire after at most 60 minutes, are single use, and are retained only as salted hashes. Worker token hashes are never returned in API records.

## Current boundary

This is a development foundation, not a production SaaS control plane. The service is currently in-memory so local development needs no additional service. Its domain API is intentionally separate from FastAPI to allow a MySQL-backed repository later. Add Redis only for distributed dispatch/cancellation, and Qdrant only when vector retrieval is enabled.

A future worker transport must be outbound HTTPS/WebSocket, carry request IDs/timeouts, and retain the existing policy, risk, approval, verification, rollback, and capability checks. Workers advertise explicit capabilities only; they do not receive arbitrary shell commands.