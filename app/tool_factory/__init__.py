"""Secure Tool Factory for JARVIS.

Creates and manages read-only DevOps tools from structured definitions:
validated, checksum-bound, versioned, RBAC-authorized, whitelisted, and
audited. Generated tools can never execute arbitrary shell — the factory
cannot represent a mutating tool, and every runtime command passes the
existing ssh_whitelist boundary before the unified executor runs it.
"""
