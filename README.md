# AI DevOps Agent / JARVIS

A read-only DevOps assistant that collects infrastructure evidence through controlled tools and supports local SQLite-backed operational memory.

## Phase 3: Intelligence and safe diagnostics

- Tool calls are bounded and repeated identical calls are skipped.
- Docker run state and Docker healthcheck state are represented separately. A running container without a healthcheck is not classified as unhealthy.
- Tool failures are returned as structured failures so the assistant can communicate uncertainty rather than inventing health conclusions.
- Health, risk, diagnostic planning, correlation, incident analysis, and read-only recommendations remain separate from infrastructure tools.

## Phase 4: Memory engine

Memory is stored locally in SQLite (default: `memory.db`). No memory telemetry or external memory service is used.

### Available memory behavior

- Memory types: episodic, semantic, procedural, preference, and failure.
- Metadata includes an identifier, title, environment, component, tags, confidence, importance, source, timestamps, provenance, outcome, and lifecycle status.
- Lifecycle statuses: active, superseded, invalid, archived.
- Deterministic retrieval filters active memories, ranks by query overlap, environment/component matches, importance, confidence, and recency, and returns a bounded result set.
- Context construction is bounded by result count and character count and explicitly states that current live evidence overrides historical memory.
- Reflections create concise incident records containing observations, diagnosis, evidence, recommendation, outcome, learning, confidence, and tags. They do not retain reasoning traces.
- Pattern detection groups persisted active episodic/failure memories into recurring incidents only when the occurrence threshold is met.
- Preferences support safe settings only: response style, units, format, and terminology.
- Consolidation supersedes exact duplicate memories while preserving provenance; low-value non-incident memories can be archived.
- Positive and negative user feedback adjust confidence and retain a redacted outcome.
- Persistence redacts common API keys, AWS credentials, passwords, authorization headers, access tokens, cookies, and SSH private keys before storing text.

## Privacy and deletion

Memory remains on the local machine in `memory.db`. Archiving a memory removes it from normal retrieval while preserving traceability. The repository memory API exposes `forget(...)` to archive matching memories; users should not place secrets in agent messages even though persistence redaction is applied.

## Remaining work before Phase 4 can be called complete

1. Add comprehensive tests for reflections, patterns, preferences, consolidation, context bounds, feedback, secret-redaction variants, and end-to-end memory-aware diagnosis.
2. Integrate memory context and outcome reflection into the actual Agent turn lifecycle, after live evidence is collected and before the final response is generated.
3. Add controlled memory tools and explicit command handling for related incidents, known solutions, component history, infrastructure relationships, remember, and forget.
4. Implement lightweight evidence-backed infrastructure relationships and tests.
5. Complete the Phase 3 CPU correction: retain load averages/core count as load-pressure evidence and only report CPU utilization when a real utilization metric is collected.
6. Add regression tests for UNKNOWN outcomes, broken Docker healthchecks, load interpretation, and duplicate tool retries.
7. Run the requested manual agent scenarios against the real interface after the above integration is complete.

## Verification

The existing test suite was last verified with:

```text
206 passed
206 tests collected
```

