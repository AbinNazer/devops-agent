-- Tool Factory persistence. Adds to the SaaS foundation without touching
-- memory.db or existing sessions. Idempotent like 001_saas_foundation.sql.

CREATE TABLE IF NOT EXISTS tool_definitions (
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    version TEXT NOT NULL DEFAULT '1.0.0',
    author TEXT NOT NULL DEFAULT 'system',
    input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_mode TEXT NOT NULL DEFAULT 'both',
    command_template TEXT NOT NULL,
    allowed_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
    timeout_seconds INTEGER NOT NULL DEFAULT 10,
    max_output_bytes INTEGER NOT NULL DEFAULT 16384,
    required_permission TEXT NOT NULL DEFAULT 'tools.execute',
    read_only BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'draft',
    provenance TEXT NOT NULL DEFAULT 'builtin',
    checksum TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, name)
);

CREATE TABLE IF NOT EXISTS tool_versions (
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    version TEXT NOT NULL,
    definition JSONB NOT NULL DEFAULT '{}'::jsonb,
    checksum TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    previous_version TEXT NOT NULL DEFAULT '',
    change_reason TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, tool_name, version)
);

CREATE INDEX IF NOT EXISTS idx_tool_versions_org_tool
    ON tool_versions (organization_id, tool_name, created_at DESC);

CREATE TABLE IF NOT EXISTS tool_permissions (
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    required_permission TEXT NOT NULL DEFAULT 'tools.execute',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, tool_name)
);

CREATE TABLE IF NOT EXISTS tool_activation_history (
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    version TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    previous_version TEXT NOT NULL DEFAULT '',
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tool_activation_org_tool
    ON tool_activation_history (organization_id, tool_name, timestamp DESC);

CREATE TABLE IF NOT EXISTS tool_execution_audits (
    audit_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    arguments_hash TEXT NOT NULL DEFAULT '',
    execution_mode TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    error_code TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    stdout_preview TEXT NOT NULL DEFAULT '',
    stderr_preview TEXT NOT NULL DEFAULT '',
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tool_execution_audits_org_tool
    ON tool_execution_audits (organization_id, tool_name, timestamp DESC);
