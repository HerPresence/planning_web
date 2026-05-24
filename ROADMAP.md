# Roadmap

Project: Metricore
Date: 2026-05-24

Metricore is a financial analytics, planning, and budgeting portal. The roadmap favors controlled architecture, secure data access, auditable imports, and a professional finance-oriented UI.

## Phase 0: Governance Foundation

Status: in progress.

Goals:

- Establish AI/development workflow.
- Record current state.
- Create system overview.
- Identify documentation gaps.
- Start controlled task/review/commit loop.

Deliverables:

- `AGENTS.md`
- `CURRENT_STATE.md`
- `ROADMAP.md`
- `docs/architecture/SYSTEM_OVERVIEW.md`
- `docs/architecture/DOCUMENTATION_GAP_ANALYSIS.md`

## Phase 1: Import Engine Stabilization

Status: next technical priority.

Goal: make all core imports auditable, staged, validated, and recoverable.

Scope:

- Define Import Engine standard.
- Document source -> preview -> mapping -> staging -> validation -> commit -> rollback lifecycle.
- Separate universal Import Engine from legacy PnL import.
- Define batch statuses and allowed transitions.
- Define validation error model.
- Document replace modes and period rules.
- Confirm RBAC/RLS behavior for imports.
- Add smoke checks for import endpoints and batch lifecycle.

Expected documents:

- `docs/standards/IMPORT_ENGINE_STANDARD.md`
- `docs/standards/BATCH_STANDARD.md`
- `docs/domains/IMPORT_SOURCES.md`

Expected code hardening:

- Batch lifecycle consistency.
- Endpoint permission coverage.
- Safer rollback/commit behavior.
- Clear handling of staging tables.
- Consistent API responses for import operations.

## Phase 2: Data Model, RBAC/RLS, and Security

Goal: make the financial data model and access rules explicit.

Scope:

- Document canonical schema.
- Map fact, plan, dimension, staging, audit, and admin tables.
- Define RBAC role/menu permission model.
- Define RLS/data-scope model.
- Decide and document behavior for empty scopes.
- Review all protected endpoints.
- Document security rules for JWT, sessions, passwords, secrets, and admin bootstrap.

Expected documents:

- `docs/domains/DATA_MODEL.md`
- `docs/standards/RBAC_STANDARD.md`
- `docs/standards/RLS_STANDARD.md`
- `docs/standards/SECURITY_STANDARD.md`

## Phase 3: PnL and Planning Engine

Goal: turn existing plan/fact screens into a controlled planning domain.

Scope:

- Define PnL hierarchy.
- Define formula and aggregation rules.
- Define plan/fact/forecast/scenario/version behavior.
- Define budget workflow and future approval flow.
- Clarify relation between PnL imports and planning data.

Expected documents:

- `docs/domains/PNL_ENGINE.md`
- `docs/architecture/PLANNING_ENGINE.md`

Potential code work:

- Planning versions.
- Scenario management.
- Forecast preparation.
- Validation of PnL structure and article mappings.

## Phase 4: Professional Finance UI

Goal: make the portal feel consistent, dense, and finance-grade.

Scope:

- Document UI patterns.
- Standardize tables, filters, pagination, modals, bulk actions, empty states, and errors.
- Standardize money, percentage, period, and variance formatting.
- Improve permission-aware UI behavior.
- Keep screens operational and compact, not marketing-like.

Expected documents:

- `docs/standards/UI_PATTERNS.md`
- `docs/standards/FRONTEND_STANDARD.md`
- `docs/standards/COMPONENT_LIBRARY.md`

## Phase 5: Deployment and Operations

Goal: make Metricore deployable and recoverable.

Scope:

- Document environments.
- Document build and deploy process.
- Define nginx/backend/frontend startup procedure.
- Define backup and recovery.
- Define smoke tests after deploy.
- Define production readiness checklist.

Expected documents:

- `docs/architecture/DEPLOYMENT.md`
- `docs/architecture/ENVIRONMENTS.md`
- `docs/architecture/BACKUP_RECOVERY.md`
- `docs/standards/TESTING_STANDARD.md`

## Later

Later product areas:

- Cash Flow engine.
- KPI library.
- Performance strategy for large datasets.
- Release notes and versioning process.
- Advanced dashboards and analytics.

## Current Decision

The next implementation focus is Import Engine stabilization.

Before Cloud Code writes code for it, Codex should prepare a task brief that includes:

- exact goal;
- files likely affected;
- forbidden changes;
- acceptance criteria;
- required checks;
- documentation updates.
