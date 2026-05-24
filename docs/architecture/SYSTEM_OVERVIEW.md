# System Overview

Project: Metricore
Date: 2026-05-24

Metricore is a financial analytics, planning, budgeting, and import portal. The current system consists of a FastAPI backend, React frontend, PostgreSQL database, ETL scripts, Import Engine, PnL modules, RBAC permissions, and RLS-like data scopes.

## High-Level Architecture

```text
Users
  |
  v
React frontend
  |
  v
FastAPI backend
  |
  v
PostgreSQL

External sources
  |
  v
ETL / Import Engine
  |
  v
Staging tables
  |
  v
Validated fact / plan / dimension tables
```

## Main Components

### Frontend

Path: `/Volumes/Temp/planning_front`

Technology:

- React
- Create React App
- CSS design tokens in `src/styles/theme.css`
- API wrapper modules under `src/api`

Responsibilities:

- Authenticated portal UI.
- Finance-oriented operational screens.
- Reference directory management.
- PnL plan/fact screens.
- Import screens.
- Admin screens for users, roles, permissions, and audit log.

Key files:

- `src/App.js`
- `src/contexts/AuthContext.js`
- `src/hooks/usePagePermission.js`
- `src/components/layout/*`
- `src/components/table/*`
- `src/components/ui/*`
- `src/pages/*`
- `src/api/*`

Current routing model:

- The app uses internal `activePage` state instead of a URL router.
- The backend can serve the built React app from a configured build directory.

### Backend

Path: `/Volumes/Temp/planning_web`

Technology:

- FastAPI
- PostgreSQL via `psycopg2`
- JWT auth
- Password hashing
- Environment-based config

Responsibilities:

- API endpoints.
- Authentication and sessions.
- Permission enforcement.
- Data-scope filtering.
- Import orchestration.
- PnL data operations.
- Reference data CRUD.
- Startup-time table creation and lightweight migrations.
- Static React build serving in deployment mode.

Key files:

- `main.py`
- `db.py`
- `config.py`
- `auth/*`
- `middleware/permission_middleware.py`
- `routers/*`
- `services/*`

### Database

Technology:

- PostgreSQL

Current schema style:

- Tables are created and adjusted by Python startup functions.
- There is no formal migration framework yet.

Observed table families:

- `dim_*`: dimensions and directories.
- `fact_*`: actual financial/operational facts.
- `plan_*`: planning data.
- `staging_*`: import staging.
- `import_*`: import configuration and batch history.
- admin tables: `users`, `roles`, `user_roles`, `menu_items`, `role_permissions`, `user_data_scope`, `user_sessions`.
- audit tables managed by `audit_service.py`.

Important risk:

- The schema must be documented and eventually moved to controlled migrations before production-grade growth.

## Core Domains

### Import Engine

Status: active and first technical priority.

Key files:

- `routers/import_engine.py`
- `services/import_engine.py`
- `routers/pnl_import.py`
- `services/article_import_service.py`
- `etl/*`

Current capabilities:

- Import types catalogue.
- Field mappings.
- Import sources.
- Import batches.
- Sales fact staging.
- Department/brand/article staging.
- Preview/load/commit/rollback-style operations.
- Bulk mapping updates for sales fact staging.

Target architecture:

```text
source
  -> preview
  -> field mapping
  -> staging load
  -> validation
  -> manual correction / bulk mapping
  -> commit to target
  -> audit / history / rollback
```

Current risk:

- Universal Import Engine and legacy PnL import overlap.
- Batch lifecycle and statuses need formal rules.
- Validation and rollback contracts need documentation and tests.

### PnL

Status: active product foundation.

Key files:

- `routers/pnl_data.py`
- `routers/pnl_import.py`
- `routers/pnl_structure.py`
- `routers/pnl_levels.py`
- `routers/articles.py`
- `etl/README_ETL.md`

Current capabilities:

- Plan/fact PnL records.
- PnL import.
- PnL structure.
- Article directory and article-to-PnL relation.
- ETL loading into `stg_pnl_olap`.

Missing architecture contract:

- PnL formula logic.
- Aggregation rules.
- Plan/fact/forecast distinctions.
- Article hierarchy ownership.
- Canonical relation between article, level1, level2, and PnL structure.

### Planning

Status: early foundation.

Current capabilities:

- `plan_pnl` records support scenario and version fields.
- UI has planning-oriented menu items.

Missing architecture contract:

- Version lifecycle.
- Scenario semantics.
- Forecast rules.
- Budget approval workflow.
- Driver-based planning.
- Freeze/copy/recalculate rules.

### RBAC

Status: implemented foundation, needs documentation and coverage review.

Key files:

- `routers/admin_access.py`
- `middleware/permission_middleware.py`
- `auth/dependencies.py`
- `auth/utils.py`
- frontend admin pages under `planning_front/src/pages`

Current model:

- Users.
- Roles.
- User-role assignments.
- Menu items.
- Role permissions with `can_view`, `can_edit`, and `can_create`.
- Admin bypass.
- Permission middleware for backend enforcement.

Risk:

- Every sensitive endpoint must be checked against the expected permission model.
- UI permission checks must not be treated as security by themselves.

### RLS / Data Scope

Status: implemented helper, needs explicit security decision.

Key files:

- `services/rls_service.py`
- `routers/pnl_data.py`
- `routers/admin_access.py`

Current model:

- `user_data_scope` contains scope type and scope value.
- Supported scope dimensions include holding, organization, region, branch, department.
- PnL data uses RLS-like filtering and write-scope checks.

Risk:

- Empty scope currently behaves as unrestricted in helper logic. This must be documented as an intentional decision or changed.
- Coverage must be mapped endpoint by endpoint.

### UI System

Status: useful component foundation, needs documented standards.

Key files:

- `planning_front/src/styles/theme.css`
- `planning_front/src/components/layout/*`
- `planning_front/src/components/table/*`
- `planning_front/src/components/ui/*`

Current capabilities:

- Layout shell and sidebar.
- Page headers and sections.
- Reusable data table.
- Filters, pagination, loading/empty states.
- Buttons, modals, selects, badges.

Target direction:

- Dense, operational finance portal.
- Consistent table/filter/modal behavior.
- Clear financial formatting for amounts, percentages, periods, and variances.
- Permission-aware actions.

## Deployment Shape

Current deployment evidence:

- `nginx.conf`
- `start_planning.bat`
- `config.py`
- frontend `package.json` proxy to `http://localhost:8002`
- backend serving React build from `T:\planning_front\build`

Current likely deployment model:

```text
nginx :80
  /static -> React build static files
  /api    -> FastAPI on 127.0.0.1:8002
  /docs   -> FastAPI docs
  /       -> React index.html
```

Missing:

- Environment map.
- Production runbook.
- Backup/restore procedure.
- Health checks.
- Service restart procedure.
- Secret management rules.

## Documentation Baseline

Current foundational docs:

- `AGENTS.md`
- `CURRENT_STATE.md`
- `ROADMAP.md`
- `docs/architecture/SYSTEM_OVERVIEW.md`
- `docs/architecture/DOCUMENTATION_GAP_ANALYSIS.md`

Next critical docs:

- `docs/standards/IMPORT_ENGINE_STANDARD.md`
- `docs/standards/BATCH_STANDARD.md`
- `docs/domains/IMPORT_SOURCES.md`
- `docs/domains/DATA_MODEL.md`
- `docs/standards/RBAC_STANDARD.md`
- `docs/standards/RLS_STANDARD.md`

## Immediate Technical Priority

Import Engine stabilization is the next technical priority.

Before code changes, prepare a Cloud Code task brief that defines:

- exact scope;
- affected files;
- forbidden changes;
- expected API behavior;
- batch lifecycle rules;
- RBAC/RLS requirements;
- documentation updates;
- verification commands.
