# Documentation Gap Analysis

Project: Metricore / Planning Web
Date: 2026-05-24
Scope: `/Volumes/Temp/planning_web` and `/Volumes/Temp/planning_front`

## Summary

The project already has working code for a financial planning portal: FastAPI backend, React frontend, PostgreSQL access, authentication, RBAC-like menu permissions, RLS-like data scopes, audit log, soft delete, PnL plan/fact data, PnL import, universal import engine, ETL staging, and reusable UI components.

The main gap is documentation and architectural contracts. Most business-critical rules exist only in code, especially in `main.py`, `routers/*`, `services/*`, frontend pages, and `theme.css`. Only two documentation files were found:

- `planning_web/etl/README_ETL.md`
- `planning_front/README.md`

Because Metricore is a financial analytics, planning, and budgeting portal, the highest-risk areas are Import Engine, Planning Engine, PnL model, RBAC/RLS, data model, deployment, and UI standards.

## Existing Evidence

Backend evidence:

- FastAPI app: `planning_web/main.py`
- PostgreSQL connection: `planning_web/db.py`
- Runtime config: `planning_web/config.py`
- Auth/JWT/passwords: `planning_web/auth/*`
- Permission middleware: `planning_web/middleware/permission_middleware.py`
- RBAC/admin tables: `planning_web/routers/admin_access.py`
- RLS helper: `planning_web/services/rls_service.py`
- Import Engine router/service: `planning_web/routers/import_engine.py`, `planning_web/services/import_engine.py`
- PnL import/data/structure: `planning_web/routers/pnl_import.py`, `planning_web/routers/pnl_data.py`, `planning_web/routers/pnl_structure.py`, `planning_web/routers/pnl_levels.py`
- ETL: `planning_web/etl/*`
- Deployment hints: `planning_web/nginx.conf`, `planning_web/start_planning.bat`

Frontend evidence:

- React app: `planning_front/src/App.js`
- API wrappers: `planning_front/src/api/*`
- Reusable layout/table/UI components: `planning_front/src/components/*`
- Auth context and permission hook: `planning_front/src/contexts/AuthContext.js`, `planning_front/src/hooks/usePagePermission.js`
- Design tokens and UI patterns: `planning_front/src/styles/theme.css`
- Domain pages: `planning_front/src/pages/*`

## Checklist Status

| Category | File | Status | Notes | Priority |
|---|---|---|---|---|
| AI Governance | `AGENTS.md` | Немає | No repo-level AI workflow/rules file found. Needed because AI will likely be used heavily in this project. | Критично зараз |
| Поточний стан | `CURRENT_STATE.md` | Немає | No stable/experimental/legacy inventory. Code already contains legacy ETL notes and mixed modules. | Критично зараз |
| План розвитку | `ROADMAP.md` | Немає | No roadmap file found. Needed to separate MVP, next modules, and later finance features. | Критично зараз |
| Архітектура | `docs/architecture/SYSTEM_OVERVIEW.md` | Немає | No full system overview. Architecture exists in code only. | Критично зараз |
| Import Engine | `docs/standards/IMPORT_ENGINE_STANDARD.md` | Немає | Code exists, standard does not. Must define staging, mapping, validation, commit, rollback, preview. | Критично зараз |
| UI Standards | `docs/standards/UI_PATTERNS.md` | Немає | UI tokens/components exist in `theme.css` and `components/*`, but no documented rules. | Критично зараз |
| RBAC | `docs/standards/RBAC_STANDARD.md` | Немає | RBAC tables and middleware exist, but role/menu/permission model is undocumented. | Критично зараз |
| RLS | `docs/standards/RLS_STANDARD.md` | Немає | `rls_service.py` exists, but scope semantics and restrictions are not documented. | Критично зараз |
| Data Model | `docs/domains/DATA_MODEL.md` | Немає | Tables are created imperatively in routers/services. No canonical schema map. | Критично зараз |
| Naming | `docs/standards/NAMING_STANDARD.md` | Немає | Mixed naming exists: `dim_*`, `fact_*`, `plan_*`, `staging_*`, frontend camelCase. Needs one standard. | Наступний етап |
| API Standards | `docs/standards/API_STANDARD.md` | Немає | API patterns exist but are inconsistent across routers. | Наступний етап |
| React Standards | `docs/standards/FRONTEND_STANDARD.md` | Немає | CRA app has components and pages but no React conventions document. | Наступний етап |
| Backend Standards | `docs/standards/BACKEND_STANDARD.md` | Немає | Backend uses routers/services, but SQL and migrations live inside code without documented layering. | Наступний етап |
| SQL Standards | `docs/standards/SQL_STANDARD.md` | Немає | No SQL naming/index/FK/audit convention. Very important before schema grows. | Наступний етап |
| Audit Log | `docs/standards/AUDIT_STANDARD.md` | Немає | `audit_service.py` and AuditLog page exist. Needs event taxonomy. | Наступний етап |
| Batch Processing | `docs/standards/BATCH_STANDARD.md` | Немає | `import_batches`, staging, and bulk update log exist. Needs lifecycle standard. | Критично зараз |
| ETL | `docs/architecture/ETL_ARCHITECTURE.md` | Є, але треба оновити | `etl/README_ETL.md` documents PnL ETL, but not at target path and not integrated with system architecture. | Наступний етап |
| Planning Engine | `docs/architecture/PLANNING_ENGINE.md` | Немає | Plan data exists in `plan_pnl`, but no budgeting engine architecture: versions, drivers, scenarios, forecast. | Критично зараз |
| PnL Engine | `docs/domains/PNL_ENGINE.md` | Немає | PnL tables/imports exist, but formula logic, structure semantics, and aggregation rules are undocumented. | Критично зараз |
| Cash Flow | `docs/domains/CASHFLOW_ENGINE.md` | Немає | Menu item exists for `cashflow`, but no implementation evidence found. | Можна пізніше |
| KPI Standards | `docs/domains/KPI_STANDARD.md` | Немає | KPI UI patterns exist, but no finance KPI definitions. | Можна пізніше |
| Technical Debt | `docs/architecture/TECH_DEBT.md` | Немає | Needed because code has legacy ETL, Windows paths, startup schema changes, and mixed import flows. | Критично зараз |
| ADR | `docs/adr/ADR-XXX.md` | Немає | No architecture decision records found. Start with decisions for DB migrations, import engine, RBAC/RLS. | Наступний етап |
| Release Notes | `docs/releases/CHANGELOG.md` | Немає | Not critical before first controlled release. | Можна пізніше |
| Environment | `docs/architecture/ENVIRONMENTS.md` | Немає | Config exists in `.env`, `config.py`, `nginx.conf`, `.bat`, but no environment map. | Наступний етап |
| Security | `docs/standards/SECURITY_STANDARD.md` | Немає | Auth/JWT/password/session code exists. Secrets and admin defaults need documented rules. | Критично зараз |
| Error Handling | `docs/standards/ERROR_HANDLING.md` | Немає | Error responses are not standardized across routers. | Наступний етап |
| Import Sources | `docs/domains/IMPORT_SOURCES.md` | Немає | Import sources are central to the app but undocumented as a domain. | Критично зараз |
| Universal Components | `docs/standards/COMPONENT_LIBRARY.md` | Немає | Reusable components exist, but usage contracts are not documented. | Наступний етап |
| Dev Workflow | `docs/standards/DEV_WORKFLOW.md` | Немає | Needed to govern AI-assisted development and avoid uncontrolled changes. | Критично зараз |
| Testing | `docs/standards/TESTING_STANDARD.md` | Немає | Some smoke scripts exist, but no test strategy. | Наступний етап |
| Deployment | `docs/architecture/DEPLOYMENT.md` | Немає | `nginx.conf` and `start_planning.bat` exist, but no deploy runbook. | Критично зараз |
| Backup/Recovery | `docs/architecture/BACKUP_RECOVERY.md` | Немає | No backup/restore strategy found. Financial data requires this before production use. | Наступний етап |
| Performance | `docs/architecture/PERFORMANCE.md` | Немає | Some pagination/indexes exist, but no performance strategy for large financial data. | Можна пізніше |

## Priority Plan

### Критично зараз

Create only the documents needed to protect current development and prevent architectural drift:

1. `AGENTS.md`
   - AI workflow, forbidden actions, approval rules, coding/documentation rules.
2. `CURRENT_STATE.md`
   - Stable, experimental, legacy modules.
   - Current frontend/backend/database status.
3. `ROADMAP.md`
   - MVP scope for Metricore and phased financial modules.
4. `docs/architecture/SYSTEM_OVERVIEW.md`
   - FastAPI, React, PostgreSQL, ETL, Import Engine, RBAC/RLS, deployment shape.
5. `docs/standards/IMPORT_ENGINE_STANDARD.md`
   - Source types, mapping, staging, validation, preview, commit, rollback, batch statuses.
6. `docs/standards/BATCH_STANDARD.md`
   - Batch lifecycle and retry/rollback/history rules.
7. `docs/standards/RBAC_STANDARD.md`
   - Roles, menu permissions, admin bypass, create/edit/view semantics.
8. `docs/standards/RLS_STANDARD.md`
   - Data scopes by holding/organization/region/branch/department.
9. `docs/domains/DATA_MODEL.md`
   - Canonical tables, relationships, ownership, fact/dim/plan/staging split.
10. `docs/architecture/PLANNING_ENGINE.md`
    - Planning versions, scenarios, budget workflow, forecast rules.
11. `docs/domains/PNL_ENGINE.md`
    - PnL article hierarchy, formulas, fact/plan aggregation, import ownership.
12. `docs/architecture/TECH_DEBT.md`
    - Known risks and cleanup queue.
13. `docs/standards/SECURITY_STANDARD.md`
    - JWT, password policy, sessions, secrets, admin bootstrap, access boundaries.
14. `docs/domains/IMPORT_SOURCES.md`
    - OLAP, SQL, Google Sheets, Excel, source metadata and owner rules.
15. `docs/standards/DEV_WORKFLOW.md`
    - Audit -> proposal -> approval -> implementation -> verification.
16. `docs/architecture/DEPLOYMENT.md`
    - Windows/server paths, nginx, ports, build, service startup, health checks.

### Наступний етап

Create once the critical documents are in place:

1. `docs/standards/UI_PATTERNS.md`
2. `docs/standards/API_STANDARD.md`
3. `docs/standards/FRONTEND_STANDARD.md`
4. `docs/standards/BACKEND_STANDARD.md`
5. `docs/standards/SQL_STANDARD.md`
6. `docs/standards/AUDIT_STANDARD.md`
7. `docs/architecture/ETL_ARCHITECTURE.md`
8. `docs/adr/ADR-001.md`, `ADR-002.md`, etc.
9. `docs/architecture/ENVIRONMENTS.md`
10. `docs/standards/ERROR_HANDLING.md`
11. `docs/standards/COMPONENT_LIBRARY.md`
12. `docs/standards/TESTING_STANDARD.md`
13. `docs/architecture/BACKUP_RECOVERY.md`

### Можна пізніше

Create when the corresponding product areas become active:

1. `docs/domains/CASHFLOW_ENGINE.md`
2. `docs/domains/KPI_STANDARD.md`
3. `docs/releases/CHANGELOG.md`
4. `docs/architecture/PERFORMANCE.md`

## Financial Portal Gaps

### Import Engine

Current state:

- Universal Import Engine exists in code.
- Import types include PnL plan/fact, sales fact, departments, brands, sales plan, expense budget, articles, article mapping, commercial conditions.
- There are field mappings, import batches, staging tables, bulk update logs, preview/load/commit/rollback routes.

Missing:

- Canonical lifecycle: source -> preview -> mapping -> staging -> validation -> commit -> rollback.
- Batch status contract and allowed transitions.
- Replace modes and period filtering rules.
- Validation error model.
- Ownership split between legacy PnL import and universal Import Engine.
- Source onboarding checklist.

### Planning Engine

Current state:

- `plan_pnl` exists and supports scenario/version fields.
- Menu contains budgets and planning sections.

Missing:

- Definition of planning versions.
- Budget approval workflow.
- Forecast logic.
- Driver-based planning rules.
- Version freeze/copy/recalculate rules.
- Separation between operational plan, budget, forecast, and scenario.

### PnL

Current state:

- PnL data, structure, import, article hierarchy, and ETL staging exist.
- `fact_pnl` and `plan_pnl` are used.

Missing:

- Canonical PnL hierarchy and formula rules.
- Article ownership and mapping rules.
- Aggregation rules by period/department/holding/organization.
- Relationship between `dim_article`, `dim_pnl_level1`, `dim_pnl_level2`, and `pnl_structure`.
- Rules for actual vs plan vs forecast.

### RBAC/RLS

Current state:

- RBAC tables: users, roles, user_roles, menu_items, role_permissions.
- Permission middleware enforces menu permissions.
- RLS-like `user_data_scope` exists and is used by PnL data.

Missing:

- Formal role catalogue for finance users.
- Matrix of menu permissions.
- Scope semantics and precedence.
- Rule for empty scopes. Current code treats empty scopes as unrestricted in several cases; this must be an explicit security decision.
- Coverage map: which endpoints are protected by RBAC, which also apply RLS.

### Data Model

Current state:

- Schema is created imperatively in Python startup functions and routers.
- Naming pattern includes `dim_*`, `fact_*`, `plan_*`, `staging_*`.

Missing:

- Single schema reference.
- ERD or table relationship map.
- Migration strategy.
- Ownership per table.
- Required audit fields and soft-delete policy.
- Index/FK standards.

### UI Standards

Current state:

- `theme.css` defines tokens, layout, sidebar, tables, controls.
- Reusable components exist for layout, tables, buttons, modals, pagination, select, badges.

Missing:

- Documented page layout standard.
- Table/filter/pagination standard.
- Modal and form behavior rules.
- Empty/loading/error state rules.
- Permission-aware UI rules.
- Finance data formatting standard for currency, percentage, period, variance.

### Deployment

Current state:

- `nginx.conf` contains server/proxy/static config.
- `start_planning.bat` exists.
- Backend config uses Windows `T:\planning_front\build` defaults.
- Frontend proxy points to `http://localhost:8002`.

Missing:

- Dev/test/prod environment map.
- Deployment runbook.
- Service restart procedure.
- Build procedure.
- Backup/restore before deploy.
- Secrets handling.
- Health checks and smoke tests.

## Recommended Next Action

Do not create all missing files at once. Start with a small documentation foundation:

1. Create `AGENTS.md`, `CURRENT_STATE.md`, and `ROADMAP.md`.
2. Create `SYSTEM_OVERVIEW.md`, `DATA_MODEL.md`, and `IMPORT_ENGINE_STANDARD.md`.
3. Then document RBAC/RLS, PnL, Planning Engine, Security, Deployment, and Tech Debt.

This order gives Metricore enough governance to continue development safely while keeping documentation tied to the real codebase.
