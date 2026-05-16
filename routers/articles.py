from fastapi import APIRouter, Form, HTTPException
from db import get_connection
from services.article_import_service import ensure_article_columns

router = APIRouter(prefix="/api/articles")


def ensure_article_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_article (
            article_id TEXT PRIMARY KEY,
            article_name TEXT NOT NULL,
            article_type TEXT,
            level1 TEXT,
            level2 TEXT,
            pnl_id INTEGER,
            is_active BOOLEAN DEFAULT true
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


_SELECT = (
    "article_id, article_name, article_type, level1, level2, pnl_id, is_active, "
    "uid_expense_article, expense_element, expense_company, level1_olap, level2_olap"
)


def _row_to_dict(r):
    return {
        "article_id":           r[0],
        "article_name":         r[1],
        "article_type":         r[2],
        "level1":               r[3],
        "level2":               r[4],
        "pnl_id":               r[5],
        "is_active":            r[6],
        "uid_expense_article":  r[7],
        "expense_element":      r[8],
        "expense_company":      r[9],
        "level1_olap":          r[10],
        "level2_olap":          r[11],
    }


# GET
@router.get("")
def get_articles():
    ensure_article_columns()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT {_SELECT} FROM dim_article ORDER BY article_id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [_row_to_dict(r) for r in rows]


# CREATE
@router.post("")
def create_article(
    article_id:          str = Form(...),
    article_name:        str = Form(...),
    article_type:        str = Form(""),
    level1:              str = Form(""),
    level2:              str = Form(""),
    pnl_id:              int = Form(...),
    uid_expense_article: str = Form(""),
    expense_element:     str = Form(""),
    expense_company:     str = Form(""),
    level1_olap:         str = Form(""),
    level2_olap:         str = Form(""),
):
    if not pnl_id:
        raise HTTPException(
            status_code=400,
            detail="Оберіть структуру PnL (pnl_id не може бути порожнім)",
        )

    ensure_article_columns()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        f"SELECT {_SELECT} FROM dim_article WHERE article_id = %s",
        (article_id,),
    )
    existing = cur.fetchone()

    if existing:
        cur.close()
        conn.close()
        return {"status": "exists", "article": _row_to_dict(existing)}

    cur.execute(
        """
        INSERT INTO dim_article
            (article_id, article_name, article_type, level1, level2, pnl_id, is_active,
             uid_expense_article, expense_element, expense_company, level1_olap, level2_olap)
        VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s)
        """,
        (
            article_id, article_name, article_type, level1, level2, pnl_id,
            uid_expense_article, expense_element, expense_company, level1_olap, level2_olap,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "article": {
            "article_id":          article_id,
            "article_name":        article_name,
            "article_type":        article_type,
            "level1":              level1,
            "level2":              level2,
            "pnl_id":              pnl_id,
            "is_active":           True,
            "uid_expense_article": uid_expense_article,
            "expense_element":     expense_element,
            "expense_company":     expense_company,
            "level1_olap":         level1_olap,
            "level2_olap":         level2_olap,
        },
    }


# UPDATE
@router.put("/{old_article_id}")
def update_article(
    old_article_id:      str,
    article_name:        str = Form(...),
    article_type:        str = Form(""),
    level1:              str = Form(""),
    level2:              str = Form(""),
    pnl_id:              int = Form(0),
    is_active:           str = Form("true"),
    uid_expense_article: str = Form(""),
    expense_element:     str = Form(""),
    expense_company:     str = Form(""),
    level1_olap:         str = Form(""),
    level2_olap:         str = Form(""),
):
    ensure_article_columns()
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_article
        SET
            article_name        = %s,
            article_type        = %s,
            level1              = %s,
            level2              = %s,
            pnl_id              = %s,
            is_active           = %s,
            uid_expense_article = %s,
            expense_element     = %s,
            expense_company     = %s,
            level1_olap         = %s,
            level2_olap         = %s
        WHERE article_id = %s
        """,
        (
            article_name, article_type, level1, level2, pnl_id, is_active_bool,
            uid_expense_article, expense_element, expense_company, level1_olap, level2_olap,
            old_article_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
