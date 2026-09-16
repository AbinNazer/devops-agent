# Database migrations

These migrations are for the future SaaS application database only. The
existing `memory.db` and `sessions/` JSON conversations are separate and must
not be migrated or deleted automatically.

The current migration is a reviewable PostgreSQL schema draft. It is not run
by application startup and does not require PostgreSQL for local development.
Run it only after reviewing the target database, backup, rollback plan, and
deployment procedure.
