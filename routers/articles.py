from fastapi import APIRouter, Form, HTTPException
from db import get_connection

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


# GET
@router.get("")
def get_articles():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM dim_article ORDER BY article_id")
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "article_id": r[0],
                "article_name": r[1],
                "article_type": r[2],
                "level1": r[3],
                "level2": r[4],
                "pnl_id": r[5],
                "is_active": r[6],
            }
        )

    cur.close()
    conn.close()

    return result


# CREATE
@router.post("")
def create_article(
    article_id: str = Form(...),
    article_name: str = Form(...),
    article_type: str = Form(""),
    level1: str = Form(""),
    level2: str = Form(""),
    pnl_id: int = Form(...),
):
    if not pnl_id:
        raise HTTPException(status_code=400, detail="Оберіть структуру PnL (pnl_id не може бути порожнім)")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT article_id, article_name, article_type, level1, level2, pnl_id, is_active "
        "FROM dim_article WHERE article_id = %s",
        (article_id,),
    )
    existing = cur.fetchone()

    if existing:
        cur.close()
        conn.close()
        return {
            "status": "exists",
            "article": {
                "article_id": existing[0],
                "article_name": existing[1],
                "article_type": existing[2],
                "level1": existing[3],
                "level2": existing[4],
                "pnl_id": existing[5],
                "is_active": existing[6],
            },
        }

    cur.execute(
        """
        INSERT INTO dim_article
        (article_id, article_name, article_type, level1, level2, pnl_id, is_active)
        VALUES (%s,%s,%s,%s,%s,%s, true)
        """,
        (article_id, article_name, article_type, level1, level2, pnl_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "article": {
            "article_id": article_id,
            "article_name": article_name,
            "article_type": article_type,
            "level1": level1,
            "level2": level2,
            "pnl_id": pnl_id,
            "is_active": True,
        },
    }


# UPDATE
@router.put("/{old_article_id}")
def update_article(
    old_article_id: str,
    article_name: str = Form(...),
    article_type: str = Form(""),
    level1: str = Form(""),
    level2: str = Form(""),
    pnl_id: int = Form(0),
    is_active: str = Form("true"),
):
    conn = get_connection()
    cur = conn.cursor()

    is_active_bool = is_active.lower() == "true"

    cur.execute(
        """
        UPDATE dim_article
        SET
            article_name = %s,
            article_type = %s,
            level1 = %s,
            level2 = %s,
            pnl_id = %s,
            is_active = %s
        WHERE article_id = %s
        """,
        (
            article_name,
            article_type,
            level1,
            level2,
            pnl_id,
            is_active_bool,
            old_article_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}