#!/usr/bin/env python3
"""Windows SSAS ETL: OLAP_Overtrans_PNL -> stg_pnl_olap

Reads DAX query and SSAS connection params from PostgreSQL import_sources table
(same table the UI edits). Falls back to .env WINDOWS_SSAS_* if source not found.

Usage:
    python etl_pnl_ssas.py
    python etl_pnl_ssas.py --source "OLAP_Overtrans_PNL"
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from common.pg_loader import get_connection, ensure_table, replace_source_data

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from etl/ root
# ---------------------------------------------------------------------------
_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_FILE)

PG_HOST     = os.getenv("PG_HOST",     "localhost")
PG_PORT     = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "planning_db")
PG_USER     = os.getenv("PG_USER",     "")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")

SOURCE_NAME = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--source" else "OLAP_Overtrans_PNL"
PS_SCRIPT   = Path(__file__).parent / "ssas_query.ps1"
PS_BIN      = "powershell.exe"

# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent.parent / "etl_ssas.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("etl_ssas")


# ---------------------------------------------------------------------------
# Read source config from import_sources table
# ---------------------------------------------------------------------------
def load_source_config(pg_conn, source_name: str) -> dict:
    """Read SSAS params and DAX query from import_sources table."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT db_server, db_port, db_database, db_login, db_password, db_query
            FROM import_sources
            WHERE source_name = %s AND is_active IS TRUE
            LIMIT 1
            """,
            (source_name,),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            f"Source '{source_name}' not found in import_sources "
            f"(or is_active=FALSE). Check the UI settings."
        )

    db_server, db_port, db_database, db_login, db_password, db_query = row

    if not db_query or not db_query.strip():
        raise RuntimeError(
            f"Source '{source_name}' has empty db_query. Fill in DAX query in UI."
        )

    log.info("Source loaded from DB:")
    log.info("  source_name : %s", source_name)
    log.info("  db_server   : %s", db_server)
    log.info("  db_port     : %s", db_port)
    log.info("  db_database : %s", db_database)
    log.info("  db_login    : %s", db_login or "(empty = SSPI)")
    log.info("  db_query    : %d chars", len(db_query.strip()))

    # Use db_server from DB; if empty fall back to .env
    server = db_server or os.getenv("WINDOWS_SSAS_SERVER") or os.getenv("OLAP_SERVER", "localhost")
    database = db_database or os.getenv("WINDOWS_SSAS_DATABASE") or os.getenv("OLAP_DATABASE", "OLAP_Overtrans")

    return {
        "server":   server,
        "database": database,
        "login":    db_login    or os.getenv("WINDOWS_SSAS_LOGIN", ""),
        "password": db_password or os.getenv("WINDOWS_SSAS_PASSWORD", ""),
        "query":    db_query.strip(),
    }


# ---------------------------------------------------------------------------
# SSAS fetch via PowerShell
# ---------------------------------------------------------------------------
def fetch_from_ssas(cfg: dict) -> dict:
    log.info("=== FETCH FROM SSAS ===")
    log.info("Script : %s", PS_SCRIPT)
    log.info("Server : %s", cfg["server"])
    log.info("DB     : %s", cfg["database"])

    if not PS_SCRIPT.exists():
        raise RuntimeError(f"PS script not found: {PS_SCRIPT}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".dax", delete=False, encoding="utf-8"
    ) as f:
        f.write(cfg["query"])
        query_file = f.name
    log.info("DAX query written to temp file (%d chars)", len(cfg["query"]))

    try:
        cmd = [
            PS_BIN, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(PS_SCRIPT),
            "-Server",    cfg["server"],
            "-Database",  cfg["database"],
            "-QueryFile", query_file,
        ]
        if cfg["login"]:    cmd += ["-Login",    cfg["login"]]
        if cfg["password"]: cmd += ["-Password", cfg["password"]]

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

        json_line = ""
        for line in reversed(stdout.splitlines()):
            s = line.strip()
            if s.startswith("{"):
                json_line = s
                break

        if not json_line:
            raise RuntimeError(f"No JSON in PowerShell output (RC={proc.returncode})")

        data = json.loads(json_line)
        if "error" in data:
            raise RuntimeError(f"SSAS error: {data['error']}")

        log.info("SSAS OK: %d rows, %d columns", data.get("count", 0), len(data.get("columns", [])))
        return data

    except subprocess.TimeoutExpired:
        raise RuntimeError("PowerShell timeout (420s)")
    finally:
        try:
            os.unlink(query_file)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Row parsing (positional -- SUMMARIZECOLUMNS order from DAX query)
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


def build_tuples(data: dict, loaded_at: datetime) -> list:
    """Convert SSAS JSON rows to insert tuples for pg_loader.
    Column order matches SUMMARIZECOLUMNS in the stored DAX query:
      0 registrar  1 article_name  2 date  3 department_id  4 department_name
      5 article_type  6 article_id  7 article_level1  8 article_level2  9 amount
    """
    columns = data["columns"]
    tuples  = []
    for row in data["rows"]:
        def get(idx, r=row):
            col = columns[idx] if idx < len(columns) else None
            return r.get(col) if col else None
        tuples.append((
            _to_str(get(0)),
            _to_str(get(1)),
            _to_date(get(2)),
            _to_str(get(3)),
            _to_str(get(4)),
            _to_str(get(5)),
            _to_str(get(6)),
            _to_str(get(7)),
            _to_str(get(8)),
            _to_decimal(get(9)),
            SOURCE_NAME,
            loaded_at,
        ))
    return tuples


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 60)
    log.info("ETL START: Windows SSAS -> stg_pnl_olap  [source: %s]", SOURCE_NAME)
    log.info("=" * 60)

    # Connect to PostgreSQL first (need it for both config and data load)
    try:
        pg_conn = get_connection(PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD)
    except Exception as exc:
        log.error("Cannot connect to PostgreSQL: %s", exc)
        sys.exit(1)

    # Step 1: read SSAS config + DAX query from import_sources table
    try:
        cfg = load_source_config(pg_conn, SOURCE_NAME)
    except Exception as exc:
        log.error("Source config load FAILED: %s", exc)
        pg_conn.close()
        sys.exit(1)

    # Step 2: fetch data from SSAS
    try:
        data = fetch_from_ssas(cfg)
    except Exception as exc:
        log.error("SSAS fetch FAILED: %s", exc, exc_info=True)
        pg_conn.close()
        sys.exit(1)

    # Step 3: load to PostgreSQL staging table
    try:
        ensure_table(pg_conn)
        loaded_at = datetime.now(timezone.utc)
        tuples    = build_tuples(data, loaded_at)
        total     = replace_source_data(pg_conn, SOURCE_NAME, tuples, loaded_at)
        pg_conn.close()
    except Exception as exc:
        log.error("PostgreSQL load FAILED: %s", exc, exc_info=True)
        sys.exit(2)

    log.info("=" * 60)
    log.info("ETL COMPLETE. Rows loaded: %d", total)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
