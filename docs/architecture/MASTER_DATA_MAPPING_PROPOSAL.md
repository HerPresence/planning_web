# Архітектура відповідності master-даних Metricore

**Статус:** Phase 0 реалізовано (dim_brand_source + brand_source_mapping) — 2026-05-24  
**Дата:** 2026-05-24  
**Автор:** Аудит коду + архітектурний аналіз

---

## 1. Контекст і мета

Metricore є системою консолідованої управлінської звітності для кількох організацій.
Дані надходять з різних джерел: OLAP/SSAS (1C, аналітика), Excel, Google Sheets.

**Проблема:** Зовнішні довідники (бренди, підрозділи) з різних джерел мають різні коди,
назви та ієрархії. Пряме вставляння зовнішніх рядків у master-таблиці
(`dim_brand`, `dim_department`) унеможливлює:
- консолідацію звітів по кількох організаціях
- контроль якості майстер-даних
- відстеження змін у зовнішніх довідниках
- розгляд конфліктів між джерелами

**Мета:** Увесь потік даних повинен виглядати так:

```
Зовнішнє джерело (OLAP / 1C / Excel)
        ↓
staging_* (тимчасове, з валідацією)
        ↓
dim_*_source (реєстр зовнішніх об'єктів)
        ↓
*_source_mapping (відповідність: pending → mapped / rejected)
        ↓
master dim_* Metricore (еталонний довідник)
        ↓
факти / плани / звіти
```

---

## 2. Поточний стан (що є зараз)

### 2.1 Бренди / НГ

**Поточний flow:**
```
OLAP → staging_brands → commit_brands() → dim_brand (прямо)
```

`commit_brands()` в `services/import_engine.py`:
- Якщо є `brand_uid`: `INSERT INTO dim_brand ... ON CONFLICT (brand_uid) DO UPDATE`
- Якщо немає `brand_uid`: SELECT по `LOWER(brand_name)`, UPDATE або INSERT

**Проблема:** Зовнішній бренд `"Протек"` з OLAP одразу стає master-брендом Metricore.
Якщо в іншому джерелі той самий бренд зветься `"Protek"` — з'являється дублікат.

### 2.2 Підрозділи

**Поточний flow:**
```
OLAP → staging_departments → commit_departments() → dim_department (прямо)
```

`commit_departments()`:
- SELECT по `department_id = dept_uid`
- Якщо є → UPDATE, якщо немає → INSERT

**Проблема:** Підрозділ `"Львів-Захід (ТОВ Протек)"` з одного OLAP-джерела і
`"Захід-Протек"` з іншого — два різні рядки в `dim_department`, хоча це один підрозділ.

### 2.3 Статті PnL

**Поточний flow (вже виправлений):**
```
OLAP → staging_articles → commit_articles() → dim_article_source + article_source_mapping
```
Статті вже реалізовані правильно. Цей документ описує аналогічний підхід для брендів і підрозділів.

### 2.4 Факт продажів

**Поточний flow:**
```
OLAP → staging_sales_fact → commit_sales_fact() → fact_turnover
```

`commit_sales_fact()` в `services/import_engine.py`:
- Для кожного рядку staging: `SELECT id FROM dim_department WHERE department_id = dept_uid`
- `SELECT id FROM dim_brand WHERE brand_uid = pg_uid OR brand_name ILIKE pg_name`
- Якщо `master_department_id IS NULL` → рядок `invalid` ("department not found")
- Якщо `master_brand_id IS NULL` → рядок `invalid` ("brand not found")

**Проблема:** Факт шукає майстер-ID прямо по uid/name, без проміжного шару відповідності.
Якщо `dept_uid` відсутній у `dim_department` — рядок відхиляється, але причина невідома
(не існує взагалі, чи просто ще не імпортований?).

---

## 3. Цільова архітектура

### 3.1 Нові таблиці для брендів

#### `dim_brand_source` — реєстр зовнішніх брендів
```sql
CREATE TABLE dim_brand_source (
    id                   SERIAL PRIMARY KEY,
    source_id            INTEGER NOT NULL,          -- import_sources.id
    source_name          TEXT    DEFAULT '',         -- назва джерела
    source_brand_id      TEXT    NOT NULL,           -- зовнішній UID/код
    source_brand_name    TEXT    DEFAULT '',         -- зовнішня назва
    source_brand_group   TEXT    DEFAULT '',         -- зовнішня група/рівень
    source_parent_uid    TEXT    DEFAULT '',
    source_parent_name   TEXT    DEFAULT '',
    extra_fields         JSONB,                      -- некапонічні поля
    loaded_at            TIMESTAMP DEFAULT NOW(),
    is_active            BOOLEAN   DEFAULT TRUE,
    UNIQUE (source_id, source_brand_id)
);
```

#### `brand_source_mapping` — відповідність зовн. бренд → master
```sql
CREATE TABLE brand_source_mapping (
    id                SERIAL PRIMARY KEY,
    source_id         INTEGER NOT NULL,
    source_brand_id   TEXT    NOT NULL,
    master_brand_id   INTEGER,                       -- dim_brand.id (NULL якщо pending)
    mapping_status    TEXT    DEFAULT 'pending',     -- pending | mapped | rejected | auto
    confidence        NUMERIC(5,2) DEFAULT 0,
    mapped_by         INTEGER,                       -- users.id
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE (source_id, source_brand_id)
);
```

**Статуси `mapping_status`:**
| Статус | Значення |
|---|---|
| `pending` | Новий зовнішній бренд, ще не прив'язаний |
| `mapped` | Прив'язаний до master-бренду вручну або підтверджено |
| `auto` | Прив'язаний автоматично (high-confidence UUID match) |
| `rejected` | Свідомо відхилений / не існує в Metricore |

---

### 3.2 Нові таблиці для підрозділів

#### `dim_department_source` — реєстр зовнішніх підрозділів
```sql
CREATE TABLE dim_department_source (
    id                     SERIAL PRIMARY KEY,
    source_id              INTEGER NOT NULL,
    source_name            TEXT    DEFAULT '',
    source_department_id   TEXT    NOT NULL,          -- зовнішній UID
    source_department_name TEXT    DEFAULT '',
    source_organization    TEXT    DEFAULT '',
    source_branch          TEXT    DEFAULT '',
    source_region          TEXT    DEFAULT '',
    source_holding         TEXT    DEFAULT '',
    source_parent_uid      TEXT    DEFAULT '',
    source_parent_name     TEXT    DEFAULT '',
    extra_fields           JSONB,
    loaded_at              TIMESTAMP DEFAULT NOW(),
    is_active              BOOLEAN   DEFAULT TRUE,
    UNIQUE (source_id, source_department_id)
);
```

#### `department_source_mapping`
```sql
CREATE TABLE department_source_mapping (
    id                      SERIAL PRIMARY KEY,
    source_id               INTEGER NOT NULL,
    source_department_id    TEXT    NOT NULL,
    master_department_id    INTEGER,                  -- dim_department.department_id
    mapping_status          TEXT    DEFAULT 'pending',
    confidence              NUMERIC(5,2) DEFAULT 0,
    mapped_by               INTEGER,
    created_at              TIMESTAMP DEFAULT NOW(),
    updated_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE (source_id, source_department_id)
);
```

---

## 4. Потік імпорту: Бренди / НГ

### Крок 1 — Завантаження в staging (вже є)
```
POST /api/import-engine/load/{batch_id}
  → load_brands_to_staging()
  → staging_brands (validated rows)
```
Без змін. Canonical поля + extra_fields JSONB.

### Крок 2 — Preview staging (вже є)
```
GET /api/import-engine/staging/{batch_id}
  → get_brands_staging_preview()
```
Без змін.

### Крок 3 — Commit (змінити логіку)
```
POST /api/import-engine/commit/{batch_id}
  → commit_brands_to_source()   ← НОВА функція
```

**Нова логіка `commit_brands_to_source(batch_id)`:**
1. Отримати `source_id` з `import_batches`
2. Для кожного valid рядку з `staging_brands`:
   - `source_brand_id` = `brand_uid` або slug з `brand_name`
   - `UPSERT INTO dim_brand_source` (оновлювати описові поля, не торкати mapping)
   - `INSERT INTO brand_source_mapping (..., 'pending', 0) ON CONFLICT DO NOTHING`
     (не перезаписувати вже mapped/rejected)
3. Оновити `import_batches.status = 'committed'`
4. **Не писати в `dim_brand`**

### Крок 4 — Відповідність (новий UI)
Користувач відкриває **"Відповідність брендів"** і:
- Бачить список pending source brands з назвою, групою, джерелом
- Може прив'язати до існуючого master-бренду (SearchableSelect)
- Може створити новий master-бренд з source-рядку (explicit action, не авто)
- Може відхилити (rejected)
- Може запустити auto-bind по UUID (high confidence ≥ 95%)

---

## 5. Потік імпорту: Підрозділи

### Кроки 1–2 — Аналогічно брендам
`staging_departments` вже є з усіма полями.

### Крок 3 — Commit (нова логіка)
```
commit_departments_to_source(batch_id)
```
1. `UPSERT INTO dim_department_source` — оновити source-дані
2. `INSERT INTO department_source_mapping (..., 'pending') ON CONFLICT DO NOTHING`
3. **Не писати в `dim_department`**

### Крок 4 — Відповідність підрозділів
UI аналогічний ArticleSourceMappingPage:
- Фільтр по organization, region, source_id, mapping_status
- Прив'язка до master `dim_department`
- Bulk-операції (fill по організації або холдингу)
- Auto-bind по UID якщо confidence ≥ 95%

### Особливість ієрархії
При прив'язці підрозділу — система може запропонувати:
- Прив'язати автоматично parent якщо `source_parent_uid` вже mapped

---

## 6. Потік імпорту: Факт продажів (після впровадження)

### Поточний (не змінювати до готовності mapping):
```
staging_sales_fact → commit_sales_fact() → fact_turnover
(пошук dim_department по uid, dim_brand по uid/name)
```

### Цільовий (після реалізації mapping-шару):
```
staging_sales_fact → resolve_mappings() → fact_turnover (або needs_mapping)
```

**Нова логіка resolve_mappings для кожного рядку staging:**
```sql
-- Знайти master department
SELECT dsm.master_department_id
FROM department_source_mapping dsm
WHERE dsm.source_id = :source_id
  AND dsm.source_department_id = :dept_uid
  AND dsm.mapping_status IN ('mapped', 'auto')
LIMIT 1;

-- Знайти master brand
SELECT bsm.master_brand_id
FROM brand_source_mapping bsm
WHERE bsm.source_id = :source_id
  AND bsm.source_brand_id = :brand_uid
  AND bsm.mapping_status IN ('mapped', 'auto')
LIMIT 1;
```

**Статус рядку staging_sales_fact:**
| Умова | Статус | Дія |
|---|---|---|
| Обидва знайдені | `valid` | Commit в `fact_turnover` |
| Department не знайдений | `needs_mapping` | Залишити в staging |
| Brand не знайдений | `needs_mapping` | Залишити в staging |
| Обидва відсутні | `needs_mapping` | Залишити в staging |

**Важливо:** `fact_turnover` повинен писатися тільки `master_department_id` і `master_brand_id`
(INTEGER FK до `dim_department` і `dim_brand`), **не** source-uid/name.

---

## 7. Необхідні UI-екрани

### 7.1 Вже реалізовані (для статей)
- `ArticleSourceMappingPage` — відповідність статей PnL

### 7.2 Потрібно створити

#### `BrandSourceMappingPage`
- Аналог `ArticleSourceMappingPage`
- Фільтри: source_id, mapping_status, brand_group, company
- Операції: bind, reject, auto-bind UUID, bulk-fill group
- KPI: total / pending / mapped / rejected
- Кнопка "Показати факти що чекають на бренд"

#### `DepartmentSourceMappingPage`
- Аналог `ArticleSourceMappingPage`
- Фільтри: source_id, mapping_status, organization, region, branch
- Особливість: відображати `source_organization + source_branch + source_department_name`
- Bulk-fill по організації
- Кнопка "Показати факти що чекають на підрозділ"

#### `StagingPendingPanel` (розширення існуючого)
- В `ImportDataPage` після commit facts:
  показати кількість рядків у стані `needs_mapping`
  з посиланнями на BrandSourceMappingPage / DepartmentSourceMappingPage
- Кнопка "Повторно перевести в fact_turnover" після прив'язки

---

## 8. Шлях міграції

### Фаза 0 — підготовка (не ламає нічого) ✅ РЕАЛІЗОВАНО 2026-05-24
1. ✅ Створити `dim_brand_source`, `brand_source_mapping` — реалізовано в `ensure_import_engine_tables()`
2. Створити `dim_department_source`, `department_source_mapping`
3. ✅ Додати `extra_fields JSONB` до всіх staging_* таблиць (реалізовано)
4. Додати `master_department_id`, `master_brand_id` (INTEGER) до `staging_sales_fact`

### Фаза 1 — наповнення source-реєстрів
1. **One-time migration:** скопіювати існуючі `dim_brand` → `dim_brand_source` з `mapping_status='mapped'` і `master_brand_id=id`
2. **One-time migration:** скопіювати `dim_department` → `dim_department_source` аналогічно
3. Далі нові імпорти брендів/підрозділів йдуть у source-реєстри
4. `commit_brands` → `commit_brands_to_source` (нова поведінка)
5. `commit_departments` → `commit_departments_to_source`

### Фаза 2 — перехід факту продажів
1. Додати `needs_mapping` як окремий статус до `staging_sales_fact`
2. `commit_sales_fact` спершу викликає `resolve_mappings()`, потім пише resolved рядки
3. `needs_mapping` рядки залишаються в staging до отримання прив'язки
4. Після прив'язки → кнопка "Re-resolve staging" → повторний commit для needs_mapping

### Зворотна сумісність
- `dim_brand` і `dim_department` **не видаляти** — залишаються master-довідниками
- `BrandsPage` і `DepartmentsPage` продовжують редагувати master-довідники напряму
- Єдина зміна: джерело поповнення master-довідників — тільки через mapping, не через import

---

## 9. Ризики і обмеження

| Ризик | Рівень | Мітигація |
|---|---|---|
| Existing `fact_turnover` має `department_uid`/`brand_uid` (текст), не FK | Середній | Залишити поточну структуру fact_turnover; додати `master_*_id` поруч |
| Якщо source brand_uid змінюється між імпортами — дублікат у source | Низький | UNIQUE (source_id, source_brand_id); оновлювати описові поля |
| Факти у staging `needs_mapping` можуть накопичуватись | Середній | UI-індикатор; автоматичне re-resolve після прив'язки |
| Паралельні джерела для одного бренду (UID різний, назва схожа) | Високий | Auto-bind тільки ≥95% confidence; решта — ручна перевірка |
| Перехідний period: import brands іде в dim_brand, нові — у source | Середній | Фаза 0+1 повинні бути виконані разом у одному release |
| `commit_departments` поки пише в `dim_department` напряму | Низький (наразі) | Зберегти поточну поведінку до Фази 1 |

---

## 10. Пріоритетність реалізації

```
Пріоритет 1 (блокує консолідацію):
  - dim_brand_source + brand_source_mapping
  - BrandSourceMappingPage
  - commit_brands → commit_brands_to_source

Пріоритет 2 (важливо для PnL):
  - dim_department_source + department_source_mapping
  - DepartmentSourceMappingPage
  - commit_departments → commit_departments_to_source

Пріоритет 3 (повна консолідація):
  - resolve_mappings() у commit_sales_fact
  - needs_mapping статус
  - Re-resolve після прив'язки
```

---

## 11. Резюме — що змінюється

| Компонент | Зараз | Після |
|---|---|---|
| commit_brands | → dim_brand напряму | → dim_brand_source + brand_source_mapping |
| commit_departments | → dim_department напряму | → dim_department_source + department_source_mapping |
| commit_articles | → dim_article_source ✅ | Без змін |
| commit_sales_fact | шукає master по uid/name | шукає через *_source_mapping |
| fact_turnover | зберігає source uid/name | зберігає master_brand_id + master_department_id |
| BrandsPage | редагує dim_brand | Без змін (master-довідник) |
| DepartmentsPage | редагує dim_department | Без змін (master-довідник) |

---

*Документ є пропозицією. Реалізація — в окремих задачах по фазах.*
