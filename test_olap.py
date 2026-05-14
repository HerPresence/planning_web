"""
OLAP / SSAS Tabular connectivity test.
Run on the Windows server: python test_olap.py

Modes
-----
  python test_olap.py ssas   — test SSAS Tabular via PowerShell + ADOMD.NET (default)
  python test_olap.py sql    — test regular SQL Server via ODBC Driver 17/18

Transport for SSAS: PowerShell script tools/read_ssas_dax.ps1
  - No pyadomd or pythonnet required
  - Uses MSOLAP OLE DB provider via .NET ADOMD client
  - Works with system Python 3.14

Environment overrides (set before running):
  OLAP_SERVER    default: 10.1.2.17
  OLAP_PORT      default: (empty)
  OLAP_DATABASE  default: OLAP_Overtrans   (SSAS Initial Catalog)
  OLAP_CUBE      default: ProfitAndLoss    (informational only)
  OLAP_LOGIN     default: (empty = Windows auth)
  OLAP_PASSWORD  default: (empty)
  OLAP_QUERY     default: see below
"""
import sys
import os
import subprocess
import json

MODE = sys.argv[1] if len(sys.argv) > 1 else "ssas"

# ── Connection params ─────────────────────────────────────────────────────────
SERVER   = os.getenv("OLAP_SERVER",   "10.1.2.17")
PORT     = os.getenv("OLAP_PORT",     "")
DATABASE = os.getenv("OLAP_DATABASE", "OLAP_Overtrans")
CUBE     = os.getenv("OLAP_CUBE",     "ProfitAndLoss")
LOGIN    = os.getenv("OLAP_LOGIN",    "")
PASSWORD = os.getenv("OLAP_PASSWORD", "")

DATA_SOURCE = f"{SERVER}:{PORT}" if PORT else SERVER

DEFAULT_DAX = (
    "EVALUATE\n"
    "SUMMARIZECOLUMNS(\n"
    '    "RowCount", COUNTROWS(\'$\')\n'
    ")"
)
DEFAULT_SQL = "SELECT TOP 3 * FROM INFORMATION_SCHEMA.TABLES"

QUERY = os.getenv("OLAP_QUERY", DEFAULT_DAX if MODE == "ssas" else DEFAULT_SQL)

print(f"Режим:  {MODE.upper()}")
print(f"Сервер: {DATA_SOURCE}  |  Каталог: {DATABASE}  |  Модель: {CUBE}")
print(f"Запит:  {QUERY[:120]}")
print()

# ── SSAS Tabular via PowerShell + ADOMD.NET ───────────────────────────────────
if MODE == "ssas":
    # Locate PowerShell script relative to this file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ps_script  = os.path.join(script_dir, "tools", "read_ssas_dax.ps1")

    print(f"PowerShell script: {ps_script}")
    if not os.path.isfile(ps_script):
        print(f"[FAIL] Script not found: {ps_script}")
        sys.exit(1)
    print("[OK] Script found.")
    print()
    print("INFO: Transport = PowerShell + ADOMD.NET (OLE DB). pyadomd/pythonnet not required.")
    print()

    cmd = [
        "powershell.exe",
        "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", ps_script,
        "-Server", DATA_SOURCE,
        "-Database", DATABASE,
        "-Query", QUERY,
        "-MaxRows", "3",
    ]
    if LOGIN:
        cmd += ["-Login", LOGIN]
    if PASSWORD:
        cmd += ["-Password", PASSWORD]

    print(f"Using PS script: {ps_script}")
    print(f"Виклик PowerShell: powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File {ps_script}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError:
        print("[FAIL] powershell.exe не знайдено. Запускайте скрипт на Windows-сервері.")
        sys.exit(2)
    except subprocess.TimeoutExpired as e:
        print("[FAIL] PowerShell timeout after 60 seconds.")
        print(f"Command: {' '.join(cmd)}")
        partial = (e.stdout or "")
        if partial.strip():
            print("--- partial stdout (last lines before timeout) ---")
            for line in partial.splitlines()[-30:]:
                print(line)
            print("--- end partial stdout ---")
        else:
            print("(no stdout captured before timeout -- process may have hung before first Write-Output)")
        sys.exit(3)

    print(f"PowerShell exit code: {proc.returncode}")
    if proc.stderr.strip():
        print(f"--- stderr ---")
        print(proc.stderr.strip())
        print(f"--- end stderr ---")

    # Print ALL stdout lines so debug markers are visible
    print(f"--- stdout (all {len((proc.stdout or '').splitlines())} lines) ---")
    for line in (proc.stdout or "").splitlines():
        print(line)
    print(f"--- end stdout ---")

    stdout = (proc.stdout or "").strip()
    if not stdout:
        print("[FAIL] PowerShell не повернув жодних даних.")
        sys.exit(4)

    # Find JSON line (last line starting with '{')
    json_line = ""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            json_line = line
            break

    if not json_line:
        print(f"[FAIL] JSON не знайдено у виводі:\n{stdout[:400]}")
        sys.exit(5)

    try:
        data = json.loads(json_line)
    except json.JSONDecodeError as e:
        print(f"[FAIL] Невалідний JSON: {e}\nraw: {json_line[:300]}")
        sys.exit(6)

    if "error" in data:
        print(f"[FAIL] SSAS помилка: {data['error']}")
        print()
        print("Перевірте:")
        print(f"  - сервер {SERVER} доступний (ping, TCP port 2383/2382)")
        print(f"  - каталог '{DATABASE}' існує в SSAS")
        print(f"  - модель '{CUBE}' розгорнута в SSAS")
        print(f"  - ADOMD.NET встановлено (C:\\Program Files\\Microsoft Analysis Services\\AS OLEDB\\...)")
        if "searched" in data:
            print(f"  - Перевірені шляхи: {data['searched']}")
        sys.exit(7)

    columns = data.get("columns", [])
    rows    = data.get("rows", [])
    count   = data.get("count", len(rows))

    print(f"[OK] Підключення до SSAS успішне!")
    print(f"Колонки ({len(columns)}): {columns}")
    print(f"Рядків отримано: {count}")
    print()
    for i, row in enumerate(rows[:3], 1):
        print(f"  Row {i}: {row}")
    print()
    print("[OK] SSAS DAX import готовий до роботи!")
    sys.exit(0)


# ── SQL Server via ODBC (pyodbc) ──────────────────────────────────────────────
if MODE == "sql":
    try:
        import pyodbc
        print("[OK] pyodbc встановлено")
    except ImportError:
        print("[FAIL] pyodbc не встановлено. Запустіть: pip install pyodbc")
        sys.exit(1)

    all_drivers = pyodbc.drivers()
    mssql_drivers = [d for d in all_drivers if "sql server" in d.lower()]
    print(f"SQL Server ODBC драйвери: {mssql_drivers or ['НЕ ЗНАЙДЕНО']}")
    print()

    ODBC_PRIORITY = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ]

    for driver in ODBC_PRIORITY:
        if driver not in all_drivers:
            print(f"  [skip] {driver!r} — не встановлено")
            continue
        db_part = f";DATABASE={DATABASE}" if DATABASE else ""
        if LOGIN and PASSWORD:
            conn_str = (
                f"DRIVER={{{driver}}};SERVER={DATA_SOURCE}{db_part};"
                f"UID={LOGIN};PWD={PASSWORD};TrustServerCertificate=yes"
            )
        else:
            conn_str = (
                f"DRIVER={{{driver}}};SERVER={DATA_SOURCE}{db_part};"
                f"Trusted_Connection=yes;TrustServerCertificate=yes"
            )
        try:
            conn = pyodbc.connect(conn_str, timeout=10)
            cur  = conn.cursor()
            cur.execute(QUERY)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(3)
            conn.close()
            print(f"[OK] Підключено через {driver!r}")
            print(f"  Колонки: {cols}")
            for r in rows:
                print(f"  {dict(zip(cols, r))}")
            sys.exit(0)
        except Exception as e:
            print(f"  [FAIL] {driver!r}: {e}")

    print()
    print("[FAIL] Всі SQL ODBC спроби невдалі.")
    sys.exit(3)
