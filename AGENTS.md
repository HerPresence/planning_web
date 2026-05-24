# AGENTS.md

Project: Metricore
Purpose: financial analytics, planning, budgeting, and import portal.

This file defines how AI assistants and developers must work in this project. It is the control layer for Codex, Cloud Code, and any other coding assistant.

## Roles

### Product Owner

The human owner defines business priorities, approves architectural decisions, and decides when changes are ready for production.

### Codex

Codex acts as architect, reviewer, and development controller.

Responsibilities:

- Turn ideas into clear implementation tasks.
- Protect architecture, security, data model, RBAC/RLS, UI standards, and documentation quality.
- Review code written by Cloud Code or other assistants.
- Keep project state, roadmap, and architecture documents current.
- Propose commits and commit messages after review.

Codex should not silently accept broad rewrites, uncontrolled refactors, or unclear security changes.

### Cloud Code

Cloud Code acts as implementation worker.

Responsibilities:

- Implement only the assigned task.
- Keep changes scoped.
- Do not rewrite unrelated modules.
- Do not change secrets, credentials, deployment paths, or database structure unless explicitly requested.
- Update relevant tests and documentation when the task requires it.

## Standard Workflow

Every meaningful change should follow this flow:

1. Audit current state.
2. Define task and acceptance criteria.
3. Implement scoped change.
4. Review architecture and security impact.
5. Run relevant checks.
6. Update documentation.
7. Commit with a clear message.

For AI-assisted work, the preferred loop is:

1. Codex creates the task brief.
2. Cloud Code implements it.
3. Codex reviews the diff.
4. Human owner approves direction.
5. Codex helps finalize docs and commit.

## Guardrails

Do not:

- Commit `.env`, credentials, database dumps, or private keys.
- Expose values from `google_credentials.json` or any `.env` file.
- Remove existing data protection checks without explicit approval.
- Bypass `PermissionMiddleware` or `get_current_user` for protected business routes.
- Introduce import flows that write directly to production fact tables without staging or validation.
- Add broad UI redesigns while implementing a backend task.
- Create large documentation trees without approval.
- Mix unrelated fixes in one commit.

Must do:

- Keep import flows auditable.
- Keep RBAC and RLS impact visible in every relevant task.
- Preserve backwards compatibility unless a breaking change is approved.
- Document data model changes before or together with implementation.
- Prefer small, reviewable changes.
- Keep frontend behavior consistent with existing layout, table, modal, filter, and permission patterns.

## Architecture Priorities

Current first technical priority: Import Engine.

The Import Engine must become a controlled, auditable pipeline:

source -> preview -> mapping -> staging -> validation -> commit -> rollback/history.

Before expanding planning and budgeting features, Metricore needs clear standards for:

- Import batches and statuses.
- Field mapping.
- Validation errors.
- Staging tables.
- Commit and rollback rules.
- RBAC/RLS behavior during import.
- Data ownership and audit log behavior.

## Documentation Rules

When a task changes system behavior, update at least one of:

- `CURRENT_STATE.md`
- `ROADMAP.md`
- `docs/architecture/SYSTEM_OVERVIEW.md`
- A domain or standard document under `docs/`

Architecture changes require either:

- Updating an existing architecture document, or
- Creating an ADR under `docs/adr/` after approval.

## Commit Rules

Commit only reviewed, coherent changes.

Recommended commit message format:

```text
area: short imperative summary
```

Examples:

```text
docs: add foundational architecture docs
import-engine: document batch lifecycle
rbac: enforce permissions on import endpoints
```

Avoid commits that combine docs, frontend redesign, backend schema changes, and deployment edits unless they are one approved release unit.
