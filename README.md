# apps/

Deployment-facing Testamur applications live here.

This directory is intentionally separate from the canonical `testamur/` Python package. Applications may depend on Testamur core, but must not redefine Testamur semantics.

Planned split:

- `apps/web/` — hosted/product web frontend
- `apps/api/` — hosted API boundary
- `apps/worker/` — background jobs, ingestion, alerts, hosted sync

Rules:

1. `testamur/` remains the semantic source of truth.
2. Hosted auth, billing, accounts, tenancy, and deployment configuration belong here, not in core.
3. Public/local Web projections may call Testamur product APIs; they must not create a parallel policy/reliance/lineage engine.
4. This directory is a migration boundary. Production code should move here only after the 0.1 convergence branch lands cleanly.
