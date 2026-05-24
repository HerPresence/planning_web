# Current State

Project: Metricore
Date: 2026-05-24

Metricore is currently a working planning portal prototype with real backend, frontend, authentication, permissions, import, ETL, PnL, and reference-data modules. The product direction is a professional financial analytics, planning, and budgeting portal.

## Repository Layout

Current mounted project paths:

- Backend/API: `/Volumes/Temp/planning_web`
- Frontend: `/Volumes/Temp/planning_front`
- PostgreSQL data directory: `/Volumes/Temp/pg_data`

Important note: the project is currently on a mounted network volume. Development and git operations may be slower than on a local disk.

## Backend State

Status: active prototype / early product foundation.

Stack:

- FastAPI
- PostgreSQL via `psycopg2`
- JWT auth via `python-jose`
- Password hashing via `passlib`
- Environment config via `.env`

Key files:

- `main.py` wires routers, middleware, startup table creation, and React build serving.
- `db.py` provides database connection.
- `config.py` holds app and database config.
- `auth/*` handles JWT, password policy, and auth dependencies.
- `middleware/permission_middleware.py` enforces menu-level permissions.
- `routers/*` contains API endpoints and many table bootstrap functions.
- `services/*` contains import, audit, RLS, soft-delete, and mapping logic.

## Frontend State

Status: active prototype / early product foundation.

Stack:

- React
- Create React App
- Axios-based API wrappers
- CSS design tokens in `src/styles/theme.css`

Key files:

- `src/App.js` controls page navigation and page rendering.
- `src/api/*` contains API wrappers.
- `src/components/*` contains reusable layout, table, modal, pagination, select, and UI components.
- `src/pages/*` contains business screens.
- `src/contexts/AuthContext.js` handles authentication state.
- `src/hooks/usePagePermission.js` supports permission-aware UI behavior.

## Stable Modules

These areas appear to have enough structure to be treated as active product foundations, although they still need documentation and tests:

- User authentication and session handling.
- Admin users, roles, menu items, and permissions.
- Reference directories: articles, departments, holdings, organizations, regions, branches, sources, brands.
- PnL plan/fact data screens and APIs.
- PnL structure and PnL levels.
- Audit log service and UI page.
- Reusable frontend tables, layout, modal, pagination, and basic controls.

## Experimental / Needs Control

These modules exist but need architectural hardening before expansion:

- Universal Import Engine.
- Import batches and staging flows.
- Sales fact import and `fact_turnover`.
- Department/brand/article import flows.
- RLS/data scopes.
- Planning scenarios and versions.
- Deployment model.

## Legacy / Compatibility Areas

These areas should not be deleted casually because they may still support existing data or deployment flows:

- `etl/etl_pnl_olap.py`
- `etl/ssas_query.ps1`
- `etl/setup_mac.sh`
- Windows-specific paths such as `T:\planning_front\build`
- Legacy PnL import endpoints under `routers/pnl_import.py`
- Existing `.bat` operational scripts

## Known Architecture Risks

- Database schema is created imperatively in Python startup and router/service functions, not through a formal migration system.
- Documentation is behind the code.
- Import Engine and legacy PnL import overlap.
- Empty RLS scopes may behave as unrestricted access; this needs an explicit security decision.
- Deployment paths are Windows-specific in several places.
- Some tests are smoke/import scripts, not a systematic test suite.
- Secrets exist in local files and must not be exposed or committed.

## Current Priority

Priority 1: stabilize and document Import Engine.

The goal is to make import safe, auditable, and predictable before adding more budgeting/planning features.

Priority 2: document and enforce Data Model, RBAC/RLS, and Planning/PnL architecture.

Priority 3: refine UI standards so the product feels like a professional finance portal, not a collection of screens.
