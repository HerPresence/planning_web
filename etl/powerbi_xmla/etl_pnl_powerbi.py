#!/usr/bin/env python3
"""Power BI XMLA ETL: Semantic Model -> stg_pnl_olap

Status: CONFIG-READY STUB
The connection and query logic is prepared but requires:
  1. Power BI Premium or PPU workspace
  2. XMLA endpoint enabled in tenant settings
  3. Service principal or user credentials configured in .env

Run:
    python etl_pnl_powerbi.py

When the XMLA endpoint is available, this runner connects via ADOMD.NET
through the Power BI XMLA endpoint (which is SSAS-compatible) and executes
the same DAX query, writing results to stg_pnl_olap.
"""

import logging
import os
import sys
from pathlib import Path

# Allow importing from parent etl/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from common.pg_loader import get_connection, ensure_table, replace_source_data

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from etl/ root
# ---------------------------------------------------------------------------
_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_FILE)

# Power BI XMLA config
XMLA_ENDPOINT   = os.getenv("POWERBI_XMLA_ENDPOINT",   "")
XMLA_DATASET    = os.getenv("POWERBI_XMLA_DATASET",    "")
XMLA_TENANT_ID  = os.getenv("POWERBI_XMLA_TENANT_ID",  "")
XMLA_CLIENT_ID  = os.getenv("POWERBI_XMLA_CLIENT_ID",  "")
XMLA_CLIENT_SEC = os.getenv("POWERBI_XMLA_CLIENT_SECRET", "")

# PostgreSQL config
PG_HOST     = os.getenv("PG_HOST",     "localhost")
PG_PORT     = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "planning_db")
PG_USER     = os.getenv("PG_USER",     "")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")

SOURCE_NAME = "PowerBI_PNL"

# DAX query template (same logic as SSAS version)
# date_from / date_to can be injected for incremental loads
DAX_QUERY_TEMPLATE = """\
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

# For incremental load (filter by date range), use:
# DAX_QUERY_TEMPLATE_FILTERED = """
# EVALUATE
# CALCULATETABLE(
#     SUMMARIZECOLUMNS(...),
#     'Д_Календар'[Дата] >= DATE({year_from},{month_from},{day_from}),
#     'Д_Календар'[Дата] <= DATE({year_to},{month_to},{day_to})
# )"""

# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent.parent / "etl_powerbi.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("etl_powerbi")


def validate_config() -> list[str]:
    """Return list of missing required config keys."""
    missing = []
    if not XMLA_ENDPOINT:  missing.append("POWERBI_XMLA_ENDPOINT")
    if not XMLA_DATASET:   missing.append("POWERBI_XMLA_DATASET")
    if not XMLA_TENANT_ID: missing.append("POWERBI_XMLA_TENANT_ID")
    if not XMLA_CLIENT_ID: missing.append("POWERBI_XMLA_CLIENT_ID")
    if not XMLA_CLIENT_SEC:missing.append("POWERBI_XMLA_CLIENT_SECRET")
    return missing


def build_connection_string() -> str:
    """Build SSAS-compatible connection string for Power BI XMLA endpoint.

    Authentication: service principal
        User ID  = app:<client_id>@<tenant_id>
        Password = <client_secret>
    """
    return (
        f"Data Source={XMLA_ENDPOINT};"
        f"Initial Catalog={XMLA_DATASET};"
        f"User ID=app:{XMLA_CLIENT_ID}@{XMLA_TENANT_ID};"
        f"Password={XMLA_CLIENT_SEC};"
        "Connect Timeout=60;"
    )


def fetch_from_powerbi() -> dict:
    """
    TODO: implement when XMLA endpoint is configured.

    Connection approach options:
    1. Windows: use ssas_query.ps1 with the XMLA endpoint as Server param
       python -> powershell -> ADODB/ADOMD.NET -> Power BI XMLA endpoint

    2. Python (any OS): use msal + requests to XMLA REST API
       pip install msal
       Token: msal.ConfidentialClientApplication(client_id, client_credential=secret, ...)
       Scope: https://analysis.windows.net/powerbi/api/.default

    3. Cross-platform (recommended for cloud runner):
       pip install semantic-link  # Microsoft Fabric SDK, includes XMLA client

    Current stub just validates config and exits with instructions.
    """
    missing = validate_config()
    if missing:
        raise RuntimeError(
            f"Power BI XMLA not configured. Missing in .env: {', '.join(missing)}\n"
            f"See README_ETL.md section 'Power BI XMLA setup'."
        )

    conn_str = build_connection_string()
    log.info("XMLA endpoint : %s", XMLA_ENDPOINT)
    log.info("Dataset       : %s", XMLA_DATASET)
    log.info("Connection str: %s", conn_str.replace(XMLA_CLIENT_SEC, "***"))

    # TODO: Implement actual XMLA query execution
    # Option A (Windows via PS):
    #   subprocess -> ssas_query.ps1 -Server XMLA_ENDPOINT -Database XMLA_DATASET
    # Option B (Python via msal + requests):
    #   token = get_msal_token(XMLA_TENANT_ID, XMLA_CLIENT_ID, XMLA_CLIENT_SEC)
    #   response = execute_xmla_query(XMLA_ENDPOINT, XMLA_DATASET, DAX_QUERY_TEMPLATE, token)
    #   data = parse_xmla_response(response)

    raise NotImplementedError(
        "Power BI XMLA fetch not yet implemented. "
        "Config is valid. See fetch_from_powerbi() for implementation options."
    )


def main():
    log.info("=" * 60)
    log.info("ETL START: Power BI XMLA -> stg_pnl_olap")
    log.info("=" * 60)

    missing = validate_config()
    if missing:
        log.error("Missing config: %s", ", ".join(missing))
        log.error("Copy .env.example to .env and fill POWERBI_XMLA_* values.")
        sys.exit(1)

    try:
        data = fetch_from_powerbi()
    except NotImplementedError as exc:
        log.warning("STUB: %s", exc)
        log.info("To implement: see comments in fetch_from_powerbi()")
        sys.exit(0)
    except Exception as exc:
        log.error("Power BI fetch FAILED: %s", exc, exc_info=True)
        sys.exit(1)

    try:
        conn = get_connection(PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD)
        ensure_table(conn)
        # tuples = build_tuples(data, ...)  # same structure as SSAS runner
        # total  = replace_source_data(conn, SOURCE_NAME, tuples)
        conn.close()
    except Exception as exc:
        log.error("PostgreSQL load FAILED: %s", exc, exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
