# ETL: PnL Import

Два production-режими імпорту PnL у PostgreSQL.
FastAPI/React читають тільки PostgreSQL — не OLAP напряму.

```
SSAS/OLAP  ──►  windows_ssas/  ──►  stg_pnl_olap  ──►  planning_web
Power BI   ──►  powerbi_xmla/  ──►  stg_pnl_olap  ──►  planning_web
```

---

## Структура

```
etl/
  common/
    pg_loader.py          # запис у stg_pnl_olap (спільний для обох)
  windows_ssas/
    ssas_query.ps1        # PowerShell: ADODB COM + ADOMD.NET fallback
    etl_pnl_ssas.py       # Python runner
    run_etl.bat           # Windows batch launcher
  powerbi_xmla/
    etl_pnl_powerbi.py    # Python runner (stub, config ready)
  .env                    # реальні credentials (не в git)
  .env.example            # шаблон
```

---

## Режим 1: Windows SSAS

**Коли використовувати:**
- Є SSAS/OLAP сервер у локальній мережі
- ETL запускається на Windows-машині з доступом до SSAS
- Типова схема: ETL на тому ж Windows-сервері де SSAS (localhost)

**Вимоги:**
- Windows з MSOLAP або SSAS client tools
- Python 3.x + venv у `planning_web/venv`
- PostgreSQL доступний з цієї машини

**Налаштування .env:**
```
WINDOWS_SSAS_SERVER=localhost
WINDOWS_SSAS_DATABASE=OLAP_Overtrans
WINDOWS_SSAS_LOGIN=          # порожньо = Windows auth (SSPI)
WINDOWS_SSAS_PASSWORD=

PG_HOST=localhost             # якщо PostgreSQL на тій самій машині
PG_PORT=5432
PG_DATABASE=planning_db
PG_USER=postgres
PG_PASSWORD=<password>
```

**Запуск:**
```bat
cd T:\planning_web\etl\windows_ssas
run_etl.bat
```

або напряму:
```bat
cd T:\planning_web\etl\windows_ssas
python etl_pnl_ssas.py
```

**Лог:** `etl/etl_ssas.log`

---

## Режим 2: Power BI XMLA

**Коли використовувати:**
- Semantic Model опублікований у Power BI Service (Premium/PPU)
- Потрібно читати дані з Power BI, а не з локального SSAS
- ETL може запускатись з Mac або хмарного сервера

**Статус:** CONFIG-READY STUB — реалізація після надання XMLA endpoint.

**Вимоги:**
- Power BI Premium або Per User (PPU) workspace
- XMLA endpoint увімкнений в налаштуваннях тенанта
- Azure AD app registration (service principal)
- Python + msal (для токену)

**Налаштування .env:**
```
POWERBI_XMLA_ENDPOINT=powerbi://api.powerbi.com/v1.0/myorg/WorkspaceName
POWERBI_XMLA_DATASET=SemanticModelName
POWERBI_XMLA_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
POWERBI_XMLA_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
POWERBI_XMLA_CLIENT_SECRET=<secret>

PG_HOST=192.168.100.11    # або localhost
PG_PASSWORD=<password>
```

**Запуск (Mac/Linux):**
```bash
cd /Volumes/Temp/planning_web/etl/powerbi_xmla
python etl_pnl_powerbi.py
```

**Лог:** `etl/etl_powerbi.log`

---

## Staging table: stg_pnl_olap

Створюється автоматично при першому запуску ETL.

```sql
CREATE TABLE IF NOT EXISTS stg_pnl_olap (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    article_name    TEXT,
    date            DATE,
    department_id   TEXT,
    department_name TEXT,
    article_type    TEXT,
    article_id      TEXT,
    article_level1  TEXT,
    article_level2  TEXT,
    amount          NUMERIC(20, 4),
    source_name     TEXT NOT NULL,   -- 'OLAP_Overtrans_PNL' або 'PowerBI_PNL'
    loaded_at       TIMESTAMPTZ NOT NULL
);
```

Replace-логіка: при кожному запуску ETL видаляє всі рядки для `source_name` і вставляє нові. Дублів немає.

---

## Перевірка результату

```sql
-- Кількість рядків по source
SELECT source_name, COUNT(*) AS rows, MAX(loaded_at) AS last_load
FROM stg_pnl_olap
GROUP BY source_name;

-- Останні завантажені дати
SELECT MIN(date), MAX(date), COUNT(*)
FROM stg_pnl_olap
WHERE source_name = 'OLAP_Overtrans_PNL';

-- Перші 10 рядків
SELECT * FROM stg_pnl_olap LIMIT 10;
```

---

## Файли (deprecated, не видаляти)

| Файл | Статус |
|---|---|
| `etl/etl_pnl_olap.py` | legacy runner (Mac attempt) |
| `etl/ssas_query.ps1` | legacy PS script |
| `etl/setup_mac.sh` | diagnostic (ADOMD.NET на Mac не підтримує SSAS) |
| `etl/lib/` | ADOMD.NET DLL для Mac (не використовується) |
