# Brand Source Mapping — Domain Documentation

## Overview

Brand Source Mapping is the process of linking source brands from OLAP/SQL imports
to master brands in `dim_brand`. It involves three layers:

| Layer              | Table               | Description                                      |
|--------------------|---------------------|--------------------------------------------------|
| Staging            | `staging_brands`    | Temporary per-batch import data, cleared after 30 days |
| Source Registry    | `dim_brand_source`  | Stable registry of all source brands ever seen  |
| Master Dictionary  | `dim_brand`         | Canonical master brand entities                 |

---

## Lifecycle

### 1. Full-Refresh OLAP Import
OLAP sources return a **complete current list** of brands on every import.

```
Import Center → Load to staging_brands (new batch)
             → Передати у відповідність (commit_brands)
             → UPSERT dim_brand_source (active brands set is_active=TRUE)
             → Soft-delete absent brands (is_active=FALSE for missing source_brand_id)
```

### 2. Source Brand States

| State                           | is_active | archived | Description                         |
|---------------------------------|-----------|----------|-------------------------------------|
| Active                          | TRUE      | FALSE    | Present in latest OLAP import        |
| Inactive                        | FALSE     | FALSE    | Missing from latest import, kept for audit |
| Archived                        | any       | TRUE     | Manually archived by SuperAdmin      |

### 3. Mapping Statuses (`brand_source_mapping`)

| Status    | Meaning                                              |
|-----------|------------------------------------------------------|
| `pending` | Not yet linked to a master brand                    |
| `mapped`  | Manually linked to dim_brand by operator            |
| `auto`    | Auto-linked by exact brand_uid match                |
| `rejected`| Operator decided this source brand has no master    |

---

## Active / Inactive Behavior

- After each `commit_brands()`, brands **absent** from the current batch are set `is_active = FALSE`
- `is_active` is restored to `TRUE` automatically on the next import if the brand reappears (UPSERT)
- `archived = FALSE` is also reset on reactivation (reappearing brand leaves archive automatically)
- **Mapping is never reset** when a brand becomes inactive

---

## Archive Rules (SuperAdmin only)

Archiving is **NOT automatic**. It requires explicit SuperAdmin action.

**Can be archived:**
- `is_active = FALSE`
- `archived = FALSE`
- `bsm.master_brand_id IS NULL` (no master binding)
- `bsm.mapping_status IN ('pending', 'rejected')` or no mapping row

**Will NOT be archived:**
- Any brand with `master_brand_id IS NOT NULL`
- Any brand with `mapping_status IN ('mapped', 'auto')`

### Cleanup Workflow
1. SuperAdmin clicks "Архівувати неактивні"
2. Preview modal shows: inactive_total, can_archive, skipped_mapped, first 20 examples
3. SuperAdmin confirms → UPDATE SET `archived=TRUE, archived_at=NOW(), archived_by=user_id`
4. Archived brands hidden from default view (visibility filter: "active")

### Restore from Archive
SuperAdmin can restore any archived brand:
- Sets `archived=FALSE, archived_at=NULL, archived_by=NULL`
- Brand becomes visible again in default view if `is_active=TRUE`

---

## Source Changed Detection

When a subsequent import changes a brand's fields (`source_brand_name`, `source_brand_group`,
`source_parent_uid`, `source_parent_name`, `source_level`, `source_company_name`, `source_is_active`):

- `source_changed = TRUE`
- `changed_fields = ["source_brand_name", ...]` (JSON array)
- `previous_snapshot = { field: old_value, ... }` (JSON)
- Badge "Source змінено" shown in Correspondence UI
- **Mapping is NOT reset** — operator reviews manually

---

## SuperAdmin Role

SuperAdmin = `is_admin = TRUE` AND has role `SuperAdmin` in `user_roles`.

Seeded automatically at startup in `routers/admin_access.py`.
Default: `admin@metricore.com.ua` is assigned SuperAdmin role.

**SuperAdmin-only actions:**
- `POST /api/brand-source-mapping/cleanup-inactive-brands`
- `POST /api/brand-source-mapping/restore-from-archive`
- `GET  /api/brand-source-mapping/cleanup-preview`

---

## API Endpoints

| Method | Path                                   | Auth          | Description                    |
|--------|----------------------------------------|---------------|--------------------------------|
| GET    | `/staged`                              | user          | List source brands + KPIs      |
| POST   | `/bind`                                | user          | Bind source to master          |
| POST   | `/reject`                              | user          | Reject source brand            |
| POST   | `/unmap`                               | user          | Reset binding to pending       |
| POST   | `/create-master-from-mapping`          | user          | Create dim_brand from source   |
| POST   | `/auto-bind`                           | user          | Auto-bind by brand_uid match   |
| GET    | `/cleanup-preview`                     | superadmin    | Preview what would be archived |
| POST   | `/cleanup-inactive-brands`             | superadmin    | Archive unbound inactive brands|
| POST   | `/restore-from-archive`                | superadmin    | Restore archived brand         |

### GET /staged — visibility parameter

| visibility | SQL filter                                              |
|------------|---------------------------------------------------------|
| `active`   | `is_active=TRUE AND archived=FALSE`  (default)         |
| `inactive` | `is_active=FALSE AND archived=FALSE`                   |
| `archived` | `archived=TRUE`                                        |
| `all`      | no filter                                              |

---

## Key Database Columns

### `dim_brand_source`

| Column              | Type      | Description                          |
|---------------------|-----------|--------------------------------------|
| `is_active`         | BOOLEAN   | Present in latest import             |
| `archived`          | BOOLEAN   | Manually archived by SuperAdmin      |
| `archived_at`       | TIMESTAMP | When archived                        |
| `archived_by`       | INTEGER   | user.id who archived                 |
| `archive_reason`    | TEXT      | Reason text                         |
| `source_changed`    | BOOLEAN   | Fields changed vs previous import    |
| `changed_fields`    | JSONB     | List of changed field names          |
| `previous_snapshot` | JSONB     | Previous values before change        |
| `seen_count`        | INTEGER   | How many times seen in imports       |
| `last_seen_at`      | TIMESTAMP | Last import timestamp                |
| `source_level`      | TEXT      | From OLAP `Level_1` (alias)          |
| `source_company_name`| TEXT     | From OLAP `Company` (alias)          |
| `source_is_active`  | TEXT      | From OLAP `valid` (alias)            |
| `source_brand_ref_id`| TEXT     | From OLAP `brand_id` (alias)         |

---

## Frontend Visibility

Default view shows **active** brands only (is_active=TRUE AND archived=FALSE).

KPI pills (click to filter):
- **Активні** — active+not archived
- **Неактивні** — inactive, not archived
- **Архів** — archived
- **Всі** — all records

---

## Prohibited Actions

- Do NOT hard-delete `dim_brand_source` records with `master_brand_id IS NOT NULL`
- Do NOT reset `brand_source_mapping` on re-import
- Do NOT create master brands for inactive/archived source brands without review
- Do NOT modify `mapped_by` field during unmap
