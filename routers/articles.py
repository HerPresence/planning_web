from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/articles")


def ensure_article_table():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_article (
            article_id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
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
    article_name: str = Form(...),
    article_type: str = Form(""),
    level1: str = Form(""),
    level2: str = Form(""),
    pnl_id: int = Form(0),
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO dim_article
        (article_name, article_type, level1, level2, pnl_id, is_active)
        VALUES (%s,%s,%s,%s,%s, true)
        RETURNING article_id
        """,
        (article_name, article_type, level1, level2, pnl_id),
    )
    new_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "article": {
            "article_id": new_id,
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
    old_article_id: int,
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