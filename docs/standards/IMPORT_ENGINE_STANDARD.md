# Стандарт Import Engine — Metricore

**Версія:** 1.0  
**Дата:** 2026-05-24  
**Статус:** Діючий стандарт  

---

## Зміст

1. [Загальний принцип](#1-загальний-принцип)
2. [Типи імпорту](#2-типи-імпорту)
3. [Правила для статей PnL (articles)](#3-правила-для-статей-pnl-articles)
4. [Правила для брендів / НГ (brands)](#4-правила-для-брендів--нг-brands)
5. [Правила для підрозділів (departments)](#5-правила-для-підрозділів-departments)
6. [Правила для факту продажів (sales_fact)](#6-правила-для-факту-продажів-sales_fact)
7. [Canonical fields і extra_fields](#7-canonical-fields-і-extra_fields)
8. [Batch lifecycle](#8-batch-lifecycle)
9. [Правила безпеки](#9-правила-безпеки)
10. [Заборонено](#10-заборонено)
11. [Пов'язані документи](#11-повязані-документи)

---

## 1. Загальний принцип

Кожен тип даних у Metricore проходить єдиний конвеєр імпорту. Жодний тип не отримує привілею "прямого запису" в master-таблиці минаючи цей конвеєр.

### Базовий потік

```
Зовнішнє джерело (OLAP / 1C / Excel / Google Sheets)
        │
        ▼
  Raw Preview                ← попередній перегляд без збереження
        │
        ▼
  Field Mapping              ← збережений маппінг полів source → target
        │
        ▼
  Staging (staging_*)        ← тимчасова таблиця з валідацією
        │
        ▼
  Validation                 ← перевірка обов'язкових полів, типів, унікальності
        │
        ├─── [dim-тип] ──▶  Source Registry (dim_*_source)
        │                         │
        │                         ▼
        │                   Source Mapping (*_source_mapping)
        │                         │
        │                         ▼
        │                   Master DIM (dim_*)          ← тільки після ручного/авто зв'язування
        │
        └─── [fact-тип] ──▶ FACT table (fact_turnover)  ← тільки після resolve dimensions
```

### Інваріанти конвеєра

- Кожен запуск імпорту створює один `import_batch` із унікальним `batch_id`.
- Staging-таблиця завжди прив'язана до `batch_id`.
- Commit = атомарна операція; або повністю успішна, або rollback.
- Після commit staging-рядки отримують статус `committed` — не видаляються.
- Raw-файл або посилання на OLAP-запит має зберігатися в `import_batches` (цільовий стан; конкретне поле уточнюється при production hardening).

---

## 2. Типи імпорту

| Тип | Staging-таблиця | Поточна цільова таблиця | Цільовий стан |
|---|---|---|---|
| `articles` | `staging_articles` | `dim_article_source` + `article_source_mapping` | ✅ вже реалізовано |
| `brands` | `staging_brands` | поки `dim_brand` (Phase 0 підготовлено) | Phase 1: `dim_brand_source` → `brand_source_mapping` → `dim_brand` |
| `departments` | `staging_departments` | поки `dim_department` | Phase 1: `dim_department_source` → `department_source_mapping` → `dim_department` |
| `sales_fact` | `staging_sales_fact` | `fact_turnover` | Phase 2: тільки після resolve master dimensions |
| `pnl_fact` | окремий flow (PnlImportPage) | `fact_pnl` | поза Universal Import Engine |
| `pnl_plan` | окремий flow (PnlImportPage) | `plan_pnl` | поза Universal Import Engine |

### pnl_fact та pnl_plan

PnL-факт і PnL-план керуються через `PnlImportPage` і **не є частиною Universal Import Engine lifecycle**.  
Вони не використовують `import_batches`, `staging_*` або universal commit flow.  
`import_sources` може використовуватися як довідник джерел, але повний pipeline (staging → validation → commit) — поза PnL-flow.  
Зміни в Import Engine не повинні зачіпати PnlImportPage.

---

## 3. Правила для статей PnL (articles)

### Поточний стан: ✅ реалізовано

```
staging_articles
        │
        ▼
  dim_article_source          ← реєстр статей з усіх джерел
        │
        ▼
  article_source_mapping      ← відповідність source_article ↔ master_article (pending / mapped / rejected)
        │
        ▼
  dim_article                 ← тільки після ручного або контрольованого зв'язування
```

### Обов'язкові правила

- `commit_articles()` **ніколи** не пише напряму в `dim_article`.
- `dim_article_source` отримує рядок для кожної унікальної `(source_id, source_article_id)`.
- `article_source_mapping` створює запис зі статусом `pending` для кожного нового source-article.
- Записи зі статусом `mapped` або `rejected` не перезаписуються автоматично.
- Canonical source_article_id = `article_uid` якщо не порожній, інакше `article_name`.

### Canonical fields для articles

```
article_uid, article_name, article_type,
level1, level2, pnl_code,
expense_element, expense_company
```

---

## 4. Правила для брендів / НГ (brands)

### Поточний стан (Phase 0)

Таблиці `dim_brand_source` і `brand_source_mapping` **вже створені** (`ensure_import_engine_tables()`).  
`commit_brands()` поки що продовжує писати в `dim_brand` напряму — це **тимчасова поведінка**.

```
[Поточний flow — тимчасово]
staging_brands → dim_brand (напряму)

[Phase 0 — таблиці готові, але не задіяні]
dim_brand_source       ← ✅ створена
brand_source_mapping   ← ✅ створена
```

### Цільовий стан (Phase 1)

```
staging_brands
        │
        ▼
  dim_brand_source            ← реєстр брендів з усіх джерел
        │
        ▼
  brand_source_mapping        ← відповідність source_brand ↔ master_brand (pending / mapped / rejected)
        │
        ▼
  dim_brand                   ← тільки після зв'язування
```

### Перехід до Phase 1

Phase 1 включає:
1. `commit_brands()` → `commit_brands_to_source()` (запис у `dim_brand_source` + `brand_source_mapping`).
2. One-time міграція: існуючі `dim_brand` записи копіюються в `dim_brand_source` зі статусом `mapped`.
3. `BrandSourceMappingPage` — UI для перегляду та ручного зв'язування.

**До Phase 1:** запис `staging_brands → dim_brand` напряму є допустимим і не порушує стандарт.  
**Після Phase 1:** запис напряму в `dim_brand` з import-потоку є забороненим.

### Canonical fields для brands

```
brand_uid, brand_name, brand_group,
parent_brand_uid, parent_brand_name
```

---

## 5. Правила для підрозділів (departments)

### Поточний стан (Phase 0 — не розпочато)

Таблиці `dim_department_source` і `department_source_mapping` **ще не створені**.  
`commit_departments()` пише напряму в `dim_department` — це **тимчасова поведінка**.

### Цільовий стан (Phase 1)

```
staging_departments
        │
        ▼
  dim_department_source       ← реєстр підрозділів з усіх джерел
        │
        ▼
  department_source_mapping   ← відповідність source_dept ↔ master_dept (pending / mapped / rejected)
        │
        ▼
  dim_department              ← тільки після зв'язування
```

### Перехід до Phase 1

Phase 1 включає:
1. `CREATE TABLE dim_department_source` + `department_source_mapping` (аналог до brand Phase 0).
2. `commit_departments()` → `commit_departments_to_source()`.
3. One-time міграція існуючих `dim_department` записів.
4. `DepartmentSourceMappingPage` — UI.

### Canonical fields для departments

```
department_uid, department_name,
organization_name, branch_name, region_name, holding_name,
parent_department_uid, parent_department_name,
separated_department_uid, separated_department_name
```

---

## 6. Правила для факту продажів (sales_fact)

### Поточний стан

```
staging_sales_fact
        │ (після валідації)
        ▼
  fact_turnover               ← запис напряму, без перевірки master dimensions
```

### Цільовий стан (Phase 2)

```
staging_sales_fact
        │
        ├─── [department і brand розпізнані] ──▶ fact_turnover
        │
        └─── [відповідність не знайдена] ──▶ залишається в staging зі статусом needs_mapping
```

### Логіка resolve (Phase 2)

1. `commit_sales_fact()` спершу викликає `resolve_dimensions()`:
   - шукає `department_uid` → `master_department_id` через `department_source_mapping`
   - шукає `brand_uid` / `product_group_uid` → `master_brand_id` через `brand_source_mapping`
2. Рядки з повним resolve → записуються в `fact_turnover`.
3. Рядки без resolve → статус `needs_mapping`, залишаються в staging.
4. Після ручного зв'язування в Mapping UI → кнопка "Re-resolve staging" → повторний commit для `needs_mapping`-рядків.

### Canonical fields для sales_fact

```
department_uid, department_name,
product_group_id, product_group_uid, product_group_name,
period_month,
sales_vat, sales_retail, excise, sales_dal, sales_kg
```

---

## 7. Canonical fields і extra_fields

### Правило

Фізичні колонки в `staging_*` таблицях створюються **тільки** для canonical fields.  
Усі інші поля, що надійшли через Field Mapping, зберігаються в `extra_fields JSONB`.

```
Mapped field
    │
    ├─── canonical? ──▶ фізична колонка в staging_*
    │
    └─── не canonical? ──▶ extra_fields JSONB
```

### Реалізація

- `CANONICAL_STAGING_FIELDS` у `services/import_engine.py` — frozenset per import type.
- `_split_canonical_extra(mapped, canonical)` — розподіляє поля при завантаженні в staging.
- `raw_row JSONB` — зберігає оригінальний рядок джерела (до маппінгу).
- `extra_fields JSONB` — зберігає зіставлені, але не-canonical поля (після маппінгу).

### Заборонено

- Виконувати `ALTER TABLE staging_*` на основі `target_field` з UI.
- Автоматично створювати фізичні колонки зі значень, введених користувачем у маппінгу.
- Ігнорувати немаплені поля без запису в `extra_fields`.

### Розширення canonical fields

Canonical fields можна розширити тільки через явну зміну коду:
1. Додати поле до `CANONICAL_STAGING_FIELDS` у `services/import_engine.py`.
2. Додати `ALTER TABLE staging_X ADD COLUMN IF NOT EXISTS` у `ensure_import_engine_tables()`.
3. Оновити `CANONICAL_FIELDS` у `ImportDataPage.js` (frontend datalist).
4. Код-рев'ю + commit із описом причини.

---

## 8. Batch lifecycle

### Статуси batch

| Статус | Опис |
|---|---|
| `loading` | Файл або OLAP-запит завантажується, staging заповнюється |
| `loaded` | Staging заповнено, валідація завершена |
| `committing` | Commit у процесі (транзакція відкрита) |
| `committed` | Commit успішний, дані записані в цільові таблиці |
| `failed` | Помилка під час завантаження або commit |
| `rolled_back` | Commit відкочено вручну або через помилку |
| `needs_mapping` | (тільки sales_fact Phase 2) є рядки без resolve dimensions |

### Дозволені переходи

```
loading ──▶ loaded ──▶ committing ──▶ committed
                  │                │
                  ▼                ▼
               failed          rolled_back

loaded ──▶ needs_mapping ──▶ committing ──▶ committed
```

### Правила переходів

- `loading → loaded`: тільки після успішного завершення завантаження всіх рядків.
- `loaded → committing`: ініціюється явним запитом користувача (кнопка Commit).
- `committing → committed`: тільки при успішному `conn.commit()`.
- `committing → failed`: при будь-якому виключенні, виконується `conn.rollback()`.
- `committed → rolled_back`: тільки ручна операція адміністратора.
- Зворотний перехід `committed → loaded` — **заборонено**.
- Один batch не може мати два паралельних commit.

---

## 9. Правила безпеки

### Автентифікація та авторизація

- Усі Import Engine endpoints (`/api/import-engine/*`) вимагають валідного JWT-токена (`get_current_user`).
- Write-endpoints (upload, map, commit, rollback) додатково перевіряють права через RBAC:
  - `canView("importSources")` — для перегляду джерел і маппінгу.
  - Адмін-тільки дії використовують `require_admin` dependency.
- Import не обходить RLS (Row-Level Security) бази даних.

### Secrets і credentials

- `.env` файл містить `DB_PASSWORD` та інші секрети — **ніколи не комітити**.
- `google_credentials.json` — **ніколи не комітити**.
- Secrets не з'являються в логах, відповідях API, або staging-даних.

### Валідація вхідних даних

- `import_type` перевіряється проти дозволеного переліку (`IMPORT_TYPES`).
- `batch_id` має перевірятися на належність поточному користувачу або джерелу (цільовий стан; production hardening).
- SQL-запити використовують параметризацію (`%s`), не f-string інтерполяцію SQL.
- Завантажені файли обмежені за розміром і типом (MIME-check).

### Аудит

- Кожна дія commit записує `user_id`, `timestamp`, `rows_loaded_to_target` у `import_batches`.
- Mapping-зміни логуються (хто і коли змінив відповідність).

---

## 10. Заборонено

Цей розділ є обов'язковим до виконання. Порушення вважається архітектурним дефектом.

### Заборонено завжди

| # | Заборона |
|---|---|
| 1 | Писати source articles напряму в `dim_article` з import-потоку |
| 2 | Комітити `sales_fact` рядки без розпізнаних master dimensions (після Phase 2) |
| 3 | Виконувати `ALTER TABLE` на основі `target_field`, введеного користувачем у UI |
| 4 | Автоматично створювати фізичні SQL-колонки зі значень маппінгу |
| 5 | Перезаписувати записи `article_source_mapping` зі статусом `mapped` або `rejected` без явного підтвердження |
| 6 | Перезаписувати `brand_source_mapping` або `department_source_mapping` зі статусом `mapped` або `rejected` |
| 7 | Змішувати зміни непов'язаних компонентів в одному git diff (наприклад, PnlImportPage + ImportDataPage) |
| 8 | Комітити `.env`, `google_credentials.json`, `*.pyc`, `__pycache__/` |
| 9 | Виконувати `git push --force` на головну гілку без явного дозволу |

### Заборонено після Phase 1 (brands і departments)

| # | Заборона |
|---|---|
| 10 | Писати source brands напряму в `dim_brand` з import-потоку |
| 11 | Писати source departments напряму в `dim_department` з import-потоку |

### Заборонено в коді Import Engine

| # | Заборона |
|---|---|
| 12 | Викликати `commit_*()` без перевірки `batch.status == 'loaded'` |
| 13 | Відкривати нову транзакцію всередині існуючої без явного savepoint |
| 14 | Повертати stack trace або внутрішні SQL-помилки в HTTP-відповіді клієнту |
| 15 | Використовувати `SELECT *` з staging без явного переліку колонок у commit-логіці |

---

## 11. Пов'язані документи

| Документ | Шлях | Опис |
|---|---|---|
| Master Data Mapping Proposal | `docs/architecture/MASTER_DATA_MAPPING_PROPOSAL.md` | Детальна архітектура source → mapping → master; DDL таблиць; фази міграції |
| Documentation Gap Analysis | `docs/architecture/DOCUMENTATION_GAP_ANALYSIS.md` | Аналіз прогалин у документації проекту |
| Current State | `CURRENT_STATE.md` | Поточний стан реалізованих функцій Metricore |
| Roadmap | `ROADMAP.md` | Пріоритети та план розвитку системи |

---

## Додаток A: Таблиці Import Engine

| Таблиця | Призначення |
|---|---|
| `import_sources` | Реєстр джерел (OLAP, Excel, тощо) |
| `import_batches` | Кожен запуск імпорту; зберігає статус, метадані, user_id |
| `import_field_mapping` | Збережені маппінги source_field → target_field per source |
| `staging_articles` | Тимчасові рядки статей до commit |
| `staging_brands` | Тимчасові рядки брендів до commit |
| `staging_departments` | Тимчасові рядки підрозділів до commit |
| `staging_sales_fact` | Тимчасові рядки факту продажів до commit |
| `dim_article_source` | Реєстр статей з усіх зовнішніх джерел |
| `article_source_mapping` | Відповідність source_article ↔ master dim_article |
| `dim_brand_source` | Реєстр брендів з усіх зовнішніх джерел (Phase 0 ✅) |
| `brand_source_mapping` | Відповідність source_brand ↔ master dim_brand (Phase 0 ✅) |
| `dim_department_source` | Реєстр підрозділів — **не створено** (Phase 1) |
| `department_source_mapping` | Відповідність source_dept ↔ master dim_department — **не створено** (Phase 1) |

---

## Додаток B: Canonical fields зведена таблиця

| Тип | Canonical fields |
|---|---|
| `articles` | `article_uid`, `article_name`, `article_type`, `level1`, `level2`, `pnl_code`, `expense_element`, `expense_company` |
| `brands` | `brand_uid`, `brand_name`, `brand_group`, `parent_brand_uid`, `parent_brand_name` |
| `departments` | `department_uid`, `department_name`, `organization_name`, `branch_name`, `region_name`, `holding_name`, `parent_department_uid`, `parent_department_name`, `separated_department_uid`, `separated_department_name` |
| `sales_fact` | `department_uid`, `department_name`, `product_group_id`, `product_group_uid`, `product_group_name`, `period_month`, `sales_vat`, `sales_retail`, `excise`, `sales_dal`, `sales_kg` |

Усі інші зіставлені поля → `extra_fields JSONB`.
