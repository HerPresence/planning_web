import csv
import io
import os
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from db import get_connection

try:
    import gspread
    from google.oauth2.service_account import Credentials
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

try:
    import openpyxl
    _OPENPYXL_AVAILABLE = True
except ImportError:
    _OPENPYXL_AVAILABLE = False

router = APIRouter(prefix="/api/pnl-import")

GOOGLE_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_CREDENTIALS_PATH",
    r"T:\planning_web\google_credentials.json",
)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


# ─── ensure tables ────────────────────────────────────────────────────────────

def ensure_article_mapping_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS article_mapping (
                mapping_id          SERIAL PRIMARY KEY,
                source_id           INTEGER,
                source_system       TEXT,
                source_article_id   TEXT,
                source_article_name TEXT,
                article_id          INTEGER,
                comment             TEXT,
                is_active           BOOLEAN DEFAULT TRUE
            )
            """
        )
        conn.commit()

        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'article_mapping' AND column_name = 'source_id'
            """
        )
        if not cur.fetchone():
            cur.execute("SET lock_timeout = '3s'")
            cur.execute(
                "ALTER TABLE article_mapping ADD COLUMN "
                "source_id INTEGER"
            )
            conn.commit()

    except Exception as exc:
        print(f"[startup] ensure_article_mapping_table warning: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


def ensure_department_mapping_table():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS department_import_mapping (
                id                       SERIAL PRIMARY KEY,
                source_id                INTEGER NOT NULL,
                external_department_code TEXT,
                external_department_name TEXT,
                internal_department_id   INTEGER NOT NULL,
                is_active                BOOLEAN DEFAULT TRUE
            )
            """
        )
        conn.commit()
    except Exception as exc:
        print(f"[startup] ensure_department_mapping_table warning: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


# ─── source reading helpers ───────────────────────────────────────────────────

def _read_google_sheet(sheet_url: str, sheet_name: Optional[str]) -> list:
    if not _GOOGLE_AVAILABLE:
        raise RuntimeError(
            "gspread / google-auth не встановлено. Встановіть: pip install gspread google-auth"
        )
    if not os.path.exists(GOOGLE_CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"Google credentials не знайдено: {GOOGLE_CREDENTIALS_PATH}"
        )
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_PATH, scopes=GOOGLE_SCOPES
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_url(sheet_url)
    ws = spreadsheet.worksheet(sheet_name) if sheet_name else spreadsheet.sheet1
    return ws.get_all_records()


def _read_file(file_bytes: bytes, filename: str) -> list:
    fname = filename.lower()
    if fname.endswith(".csv"):
        content = file_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        return [dict(row) for row in reader]
    if fname.endswith((".xlsx", ".xls")):
        if not _OPENPYXL_AVAILABLE:
            raise RuntimeError(
                "openpyxl не встановлено. Встановіть: pip install openpyxl"
            )
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        headers = [
            str(c.value).strip() if c.value is not None else f"col_{i}"
            for i, c in enumerate(ws[1])
        ]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(v is None for v in row):
                continue
            rows.append(
                {headers[i]: ("" if row[i] is None else row[i]) for i in range(len(headers))}
            )
        return rows
    raise ValueError(f"Непідтримуваний формат файлу: {filename}")


# ─── lookup helpers ───────────────────────────────────────────────────────────

def _lookup_department(cur, source_id: int, ext_code: str, ext_name: str):
    if ext_code:
        cur.execute(
            """
            SELECT internal_department_id FROM department_import_mapping
            WHERE source_id = %s AND external_department_code = %s AND is_active = TRUE
            """,
            (source_id, str(ext_code)),
        )
        found = cur.fetchall()
        if len(found) == 1:
            return found[0][0], None
        if len(found) > 1:
            return None, "department_ambiguous_mapping"

    if ext_name:
        cur.execute(
            """
            SELECT internal_department_id FROM department_import_mapping
            WHERE source_id = %s AND external_department_name = %s AND is_active = TRUE
            """,
            (source_id, str(ext_name)),
        )
        found = cur.fetchall()
        if len(found) == 1:
            return found[0][0], None
        if len(found) > 1:
            return None, "department_ambiguous_mapping"

    return None, "department_not_mapped"


def _lookup_article(cur, source_id: int, ext_code: str, ext_name: str):
    if ext_code:
        cur.execute(
            """
            SELECT article_id FROM article_mapping
            WHERE source_id = %s AND source_article_id = %s AND is_active = TRUE
            """,
            (source_id, str(ext_code)),
        )
        found = cur.fetchall()
        if len(found) == 1:
            return found[0][0], None
        if len(found) > 1:
            return None, "article_ambiguous_mapping"

    if ext_name:
        cur.execute(
            """
            SELECT article_id FROM article_mapping
            WHERE source_id = %s AND source_article_name = %s AND is_active = TRUE
            """,
            (source_id, str(ext_name)),
        )
        found = cur.fetchall()
        if len(found) == 1:
            return found[0][0], None
        if len(found) > 1:
            return None, "article_ambiguous_mapping"

    return None, "article_not_mapped"


# ─── article mapping CRUD ─────────────────────────────────────────────────────

@router.get("/article-mapping")
def get_article_mappings(source_id: Optional[int] = None):
    conn = get_connection()
    cur = conn.cursor()

    if source_id is not None:
        cur.execute(
            """
            SELECT mapping_id, source_id, source_article_id, source_article_name,
                   article_id, comment, is_active
            FROM article_mapping
            WHERE source_id = %s
            ORDER BY mapping_id DESC
            """,
            (source_id,),
        )
    else:
        cur.execute(
            """
            SELECT mapping_id, source_id, source_article_id, source_article_name,
                   article_id, comment, is_active
            FROM article_mapping
            ORDER BY mapping_id DESC
            """
        )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "mapping_id": r[0],
            "source_id": r[1],
            "source_article_id": r[2],
            "source_article_name": r[3],
            "article_id": r[4],
            "comment": r[5],
            "is_active": r[6],
        }
        for r in rows
    ]


@router.post("/article-mapping")
def create_article_mapping(
    source_id: int = Form(...),
    source_article_id: str = Form(""),
    source_article_name: str = Form(""),
    article_id: int = Form(...),
    comment: str = Form(""),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO article_mapping
            (source_id, source_article_id, source_article_name, article_id, comment, is_active)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        RETURNING mapping_id
        """,
        (source_id, source_article_id, source_article_name, article_id, comment),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok", "mapping_id": new_id}


@router.put("/article-mapping/{mapping_id}")
def update_article_mapping(
    mapping_id: int,
    source_id: int = Form(...),
    source_article_id: str = Form(""),
    source_article_name: str = Form(""),
    article_id: int = Form(...),
    comment: str = Form(""),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE article_mapping
        SET source_id = %s, source_article_id = %s, source_article_name = %s,
            article_id = %s, comment = %s, is_active = %s
        WHERE mapping_id = %s
        """,
        (
            source_id, source_article_id, source_article_name,
            article_id, comment, is_active.lower() == "true", mapping_id,
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/article-mapping/{mapping_id}")
def delete_article_mapping(mapping_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE article_mapping SET is_active = FALSE WHERE mapping_id = %s",
        (mapping_id,),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


# ─── department mapping CRUD ──────────────────────────────────────────────────

@router.get("/department-mapping")
def get_department_mappings(source_id: Optional[int] = None):
    conn = get_connection()
    cur = conn.cursor()

    if source_id is not None:
        cur.execute(
            """
            SELECT id, source_id, external_department_code, external_department_name,
                   internal_department_id, is_active
            FROM department_import_mapping
            WHERE source_id = %s
            ORDER BY id DESC
            """,
            (source_id,),
        )
    else:
        cur.execute(
            """
            SELECT id, source_id, external_department_code, external_department_name,
                   internal_department_id, is_active
            FROM department_import_mapping
            ORDER BY id DESC
            """
        )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "source_id": r[1],
            "external_department_code": r[2],
            "external_department_name": r[3],
            "internal_department_id": r[4],
            "is_active": r[5],
        }
        for r in rows
    ]


@router.post("/department-mapping")
def create_department_mapping(
    source_id: int = Form(...),
    external_department_code: str = Form(""),
    external_department_name: str = Form(""),
    internal_department_id: int = Form(...),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO department_import_mapping
            (source_id, external_department_code, external_department_name,
             internal_department_id, is_active)
        VALUES (%s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        (source_id, external_department_code, external_department_name, internal_department_id),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok", "id": new_id}


@router.put("/department-mapping/{mapping_id}")
def update_department_mapping(
    mapping_id: int,
    source_id: int = Form(...),
    external_department_code: str = Form(""),
    external_department_name: str = Form(""),
    internal_department_id: int = Form(...),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE department_import_mapping
        SET source_id = %s, external_department_code = %s,
            external_department_name = %s, internal_department_id = %s, is_active = %s
        WHERE id = %s
        """,
        (
            source_id, external_department_code, external_department_name,
            internal_department_id, is_active.lower() == "true", mapping_id,
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/department-mapping/{mapping_id}")
def delete_department_mapping(mapping_id: int):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE department_import_mapping SET is_active = FALSE WHERE id = %s",
        (mapping_id,),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


# ─── preview ──────────────────────────────────────────────────────────────────

@router.post("/preview")
async def preview_source(
    source_type: str = Form(...),
    file: Optional[UploadFile] = File(None),
    sheet_url: Optional[str] = Form(None),
    sheet_name: Optional[str] = Form(None),
):
    try:
        if source_type == "google_sheets":
            if not sheet_url:
                raise HTTPException(
                    status_code=400, detail="Потрібно вказати посилання на Google Sheet"
                )
            rows = _read_google_sheet(sheet_url, sheet_name or None)
        elif source_type == "file":
            if not file:
                raise HTTPException(status_code=400, detail="Потрібно завантажити файл")
            file_bytes = await file.read()
            rows = _read_file(file_bytes, file.filename)
        else:
            raise HTTPException(status_code=400, detail=f"Невідомий тип джерела: {source_type}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not rows:
        return {"columns": [], "preview_rows": [], "total_rows": 0}

    return {
        "columns": list(rows[0].keys()),
        "preview_rows": rows[:5],
        "total_rows": len(rows),
    }


# ─── run import ───────────────────────────────────────────────────────────────

@router.post("/run")
async def run_import(
    source_id: int = Form(...),
    import_type: str = Form(...),
    scenario: str = Form(""),
    version_name: str = Form(""),
    source_type: str = Form(...),
    file: Optional[UploadFile] = File(None),
    sheet_url: Optional[str] = Form(None),
    sheet_name: Optional[str] = Form(None),
    period_col: str = Form(...),
    dept_code_col: str = Form(""),
    dept_name_col: str = Form(""),
    article_code_col: str = Form(""),
    article_name_col: str = Form(""),
    amount_col: str = Form(...),
    comment_col: str = Form(""),
):
    try:
        if source_type == "google_sheets":
            if not sheet_url:
                raise HTTPException(
                    status_code=400, detail="Потрібно вказати посилання на Google Sheet"
                )
            rows = _read_google_sheet(sheet_url, sheet_name or None)
        elif source_type == "file":
            if not file:
                raise HTTPException(status_code=400, detail="Потрібно завантажити файл")
            file_bytes = await file.read()
            rows = _read_file(file_bytes, file.filename)
        else:
            raise HTTPException(status_code=400, detail=f"Невідомий тип джерела: {source_type}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Помилка читання джерела: {exc}")

    if not rows:
        return {"status": "ok", "imported": 0, "skipped": 0, "total_rows": 0, "errors": []}

    conn = get_connection()
    cur = conn.cursor()
    imported = 0
    skipped = 0
    errors = []

    try:
        for row_num, row in enumerate(rows, start=2):
            ext_dept_code = str(row.get(dept_code_col, "")).strip() if dept_code_col else ""
            ext_dept_name = str(row.get(dept_name_col, "")).strip() if dept_name_col else ""
            ext_art_code = str(row.get(article_code_col, "")).strip() if article_code_col else ""
            ext_art_name = str(row.get(article_name_col, "")).strip() if article_name_col else ""

            period = str(row.get(period_col, "")).strip()
            if not period:
                skipped += 1
                errors.append({"row": row_num, "type": "missing_period", "value": ""})
                continue

            raw_amount = row.get(amount_col, "")
            try:
                amount = float(str(raw_amount).replace(",", ".").strip())
            except (ValueError, TypeError):
                skipped += 1
                errors.append({"row": row_num, "type": "invalid_amount", "value": str(raw_amount)})
                continue

            dept_id, dept_err = _lookup_department(cur, source_id, ext_dept_code, ext_dept_name)
            if dept_err:
                skipped += 1
                errors.append({
                    "row": row_num,
                    "type": dept_err,
                    "value": ext_dept_code or ext_dept_name,
                })
                continue

            art_id, art_err = _lookup_article(cur, source_id, ext_art_code, ext_art_name)
            if art_err:
                skipped += 1
                errors.append({
                    "row": row_num,
                    "type": art_err,
                    "value": ext_art_code or ext_art_name,
                })
                continue

            cur.execute(
                """
                SELECT holding_name, organization_name, region_name, branch_name, department_name
                FROM dim_department WHERE department_id = %s
                """,
                (dept_id,),
            )
            dept_row = cur.fetchone()
            if not dept_row:
                skipped += 1
                errors.append({"row": row_num, "type": "department_not_found_in_dim", "value": str(dept_id)})
                continue

            holding_name, organization_name, region_name, branch_name, department_name = dept_row

            cur.execute(
                "SELECT article_name, pnl_id FROM dim_article WHERE article_id = %s",
                (art_id,),
            )
            art_row = cur.fetchone()
            if not art_row:
                skipped += 1
                errors.append({"row": row_num, "type": "article_not_found_in_dim", "value": str(art_id)})
                continue

            article_name, pnl_id = art_row
            comment = str(row.get(comment_col, "")).strip() if comment_col else ""

            if import_type == "plan":
                cur.execute(
                    """
                    INSERT INTO plan_pnl
                        (period, holding_name, organization_name, region_name, branch_name,
                         department_id, department_name, article_id, article_name, pnl_id,
                         scenario, version_name, amount, comment, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                    """,
                    (
                        period, holding_name, organization_name, region_name, branch_name,
                        str(dept_id), department_name, str(art_id), article_name,
                        str(pnl_id) if pnl_id else "",
                        scenario, version_name, amount, comment,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO fact_pnl
                        (period, holding_name, organization_name, region_name, branch_name,
                         department_id, department_name, article_id, article_name, pnl_id,
                         amount, registrar, source_name, loaded_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    """,
                    (
                        period, holding_name, organization_name, region_name, branch_name,
                        str(dept_id), department_name, str(art_id), article_name,
                        str(pnl_id) if pnl_id else "",
                        amount, "", "",
                    ),
                )

            imported += 1

        conn.commit()

    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Помилка імпорту: {exc}")
    finally:
        cur.close()
        conn.close()

    return {
        "status": "ok",
        "imported": imported,
        "skipped": skipped,
        "total_rows": len(rows),
        "errors": errors[:100],
    }
