#!/usr/bin/env python3
"""ETL: SSAS OLAP_Overtrans_PNL -> PostgreSQL stg_pnl_olap

Run: python etl_pnl_olap.py
Requires:
  - pwsh (PowerShell Core) on Mac  [brew install powershell]
  - ADOMD.NET DLL in ./lib/        [run setup_mac.sh first]
  - .env file with connection params
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OLAP_SERVER   = os.getenv("OLAP_SERVER",   "10.1.2.17")
OLAP_DATABASE = os.getenv("OLAP_DATABASE", "OLAP_Overtrans")
OLAP_LOGIN    = os.getenv("OLAP_LOGIN",    "")
OLAP_PASSWORD = os.getenv("OLAP_PASSWORD", "")

PG_HOST     = os.getenv("PG_HOST",     "10.1.2.17")
PG_PORT     = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "planning_db")
PG_USER     = os.getenv("PG_USER",     "")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")

SOURCE_NAME = "OLAP_Overtrans_PNL"
BATCH_SIZE  = 500

PS_SCRIPT = os.path.join(os.path.dirname(__file__), "ssas_query.ps1")
PS_BIN    = "powershell.exe" if sys.platform == "win32" else "pwsh"

# ---------------------------------------------------------------------------
# DAX query (Ukrainian column names, passed to PS via temp file)
# ---------------------------------------------------------------------------
DAX_QUERY = """\
EVALUATE
SUMMARIZECOLUMNS(
    ProfitAndLoss[Реєстратор],
    ProfitAndLoss[Стаття],
    'Д_Календар'[Дата],
    'Д_Підрозділи'[IDПідрозділ],
    'Д_Підрозділи'[Підрозділ],
    'Д_Стаття'[Вид],
    'Д_Стаття'[IDСтаття],
    'Д_Стаття'[Level1],
    'Д_Стаття'[Level2],
    "Сума по закритих періодах", [Сума по закритих періодах]
)"""

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
CREATE_TABLE_SQL = """
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
    source_name     TEXT NOT NULL,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stg_pnl_olap_source ON stg_pnl_olap(source_name);
CREATE INDEX IF NOT EXISTS idx_stg_pnl_olap_date   ON stg_pnl_olap(date);
"""

INSERT_SQL = """
INSERT INTO stg_pnl_olap
    (registrar, article_name, date, department_id, department_name,
     article_type, article_id, article_level1, article_level2,
     amount, source_name, loaded_at)
VALUES %s
"""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "etl_pnl_olap.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("etl_pnl")


# ---------------------------------------------------------------------------
# SSAS fetch
# ---------------------------------------------------------------------------
def fetch_from_ssas() -> dict:
    log.info("=== FETCH FROM SSAS ===")
    log.info("Script : %s", PS_SCRIPT)
    log.info("PS bin : %s", PS_BIN)
    log.info("Server : %s", OLAP_SERVER)
    log.info("DB     : %s", OLAP_DATABASE)

    if not os.path.exists(PS_SCRIPT):
        raise RuntimeError(f"PS script not found: {PS_SCRIPT}")

    # Write DAX query to temp file — avoids all shell escaping / encoding issues
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".dax", delete=False, encoding="utf-8"
    ) as f:
        f.write(DAX_QUERY)
        query_file = f.name
    log.info("DAX query written to: %s", query_file)

    try:
        cmd = [
            PS_BIN, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", PS_SCRIPT,
            "-Server",    OLAP_SERVER,
            "-Database",  OLAP_DATABASE,
            "-QueryFile", query_file,
        ]
        if OLAP_LOGIN:    cmd += ["-Login",    OLAP_LOGIN]
        if OLAP_PASSWORD: cmd += ["-Password", OLAP_PASSWORD]

        log.info("Running PowerShell...")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=420,
        )

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        for line in stdout.splitlines():
            log.info("[PS] %s", line)
        if stderr:
            for line in stderr.splitlines():
                log.warning("[PS-ERR] %s", line)

        if proc.returncode != 0:
            log.warning("PowerShell exit code: %d", proc.returncode)

        # Find last JSON object line in stdout
        json_line = ""
        for line in reversed(stdout.splitlines()):
            stripped = line.strip()
            if stripped.startswith("{"):
                json_line = stripped
                break

        if not json_line:
            raise RuntimeError(
                f"No JSON in PowerShell output (RC={proc.returncode}). "
                "Check [PS] log lines above."
            )

        data = json.loads(json_line)

        if "error" in data:
            raise RuntimeError(f"SSAS error: {data['error']}")

        row_count = data.get("count", 0)
        col_count = len(data.get("columns", []))
        log.info("SSAS OK: %d rows, %d columns", row_count, col_count)
        log.info("Columns: %s", " | ".join(data.get("columns", [])))
        return data

    except subprocess.TimeoutExpired:
        raise RuntimeError("PowerShell timeout (420s)")
    finally:
        try:
            os.unlink(query_file)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------
def _to_str(v) -> str | None:
    return str(v).strip() if v is not None else None


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    log.warning("Cannot parse date: %r", s)
    return None


def _to_decimal(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        log.warning("Cannot parse decimal: %r", v)
        return None


def parse_row(row: dict, columns: list, loaded_at: datetime) -> tuple:
    """Map SSAS row (by column position) to INSERT tuple.
    SUMMARIZECOLUMNS order:
      0  registrar       ProfitAndLoss[Реєстратор]
      1  article_name    ProfitAndLoss[Стаття]
      2  date            Д_Календар[Дата]
      3  department_id   Д_Підрозділи[IDПідрозділ]
      4  department_name Д_Підрозділи[Підрозділ]
      5  article_type    Д_Стаття[Вид]
      6  article_id      Д_Стаття[IDСтаття]
      7  article_level1  Д_Стаття[Level1]
      8  article_level2  Д_Стаття[Level2]
      9  amount          Сума по закритих періодах
    """
    def get(idx):
        col = columns[idx] if idx < len(columns) else None
        return row.get(col) if col else None

    return (
        _to_str(get(0)),      # registrar
        _to_str(get(1)),      # article_name
        _to_date(get(2)),     # date
        _to_str(get(3)),      # department_id
        _to_str(get(4)),      # department_name
        _to_str(get(5)),      # article_type
        _to_str(get(6)),      # article_id
        _to_str(get(7)),      # article_level1
        _to_str(get(8)),      # article_level2
        _to_decimal(get(9)),  # amount
        SOURCE_NAME,
        loaded_at,
    )


# ---------------------------------------------------------------------------
# PostgreSQL load
# ---------------------------------------------------------------------------
def load_to_pg(data: dict) -> int:
    log.info("=== LOAD TO POSTGRESQL ===")
    log.info("Host: %s:%d  DB: %s  User: %s", PG_HOST, PG_PORT, PG_DATABASE, PG_USER)

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
        connect_timeout=30,
    )
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            log.info("Table stg_pnl_olap ensured.")

            cur.execute(
                "DELETE FROM stg_pnl_olap WHERE source_name = %s",
                (SOURCE_NAME,),
            )
            log.info("Deleted %d old rows for source '%s'.", cur.rowcount, SOURCE_NAME)

            columns   = data["columns"]
            rows      = data["rows"]
            loaded_at = datetime.now(timezone.utc)
            total     = 0

            for i in range(0, len(rows), BATCH_SIZE):
                batch  = rows[i : i + BATCH_SIZE]
                tuples = [parse_row(r, columns, loaded_at) for r in batch]
                psycopg2.extras.execute_values(
                    cur, INSERT_SQL, tuples, page_size=BATCH_SIZE
                )
                total += len(batch)
                if total % 5000 == 0 or total == len(rows):
                    log.info("  Inserted %d / %d rows", total, len(rows))

        conn.commit()
        log.info("Commit OK. Total inserted: %d rows.", total)
        return total

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 60)
    log.info("ETL START: OLAP_Overtrans_PNL -> stg_pnl_olap")
    log.info("=" * 60)

    try:
        data = fetch_from_ssas()
    except Exception as exc:
        log.error("SSAS fetch FAILED: %s", exc, exc_info=True)
        sys.exit(1)

    try:
        total = load_to_pg(data)
    except Exception as exc:
        log.error("PostgreSQL load FAILED: %s", exc, exc_info=True)
        sys.exit(2)

    log.info("=" * 60)
    log.info("ETL COMPLETE. Rows loaded: %d", total)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
