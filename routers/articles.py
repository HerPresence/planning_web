from fastapi import APIRouter, Form
from db import get_connection

router = APIRouter(prefix="/api/articles")


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
    pnl_id: int = Form(0),
):
    conn = get_connection()
    cur = conn.cursor()

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

    return {"status": "ok"}


# UPDATE
@router.put("/{old_article_id}")
def update_article(
    old_article_id: str,
    article_id: str = Form(...),
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
        UPDATE dim_article
        SET
            article_id = %s,
            article_name = %s,
            article_type = %s,
            level1 = %s,
            level2 = %s,
            pnl_id = %s
        WHERE article_id = %s
        """,
        (
            article_id,
            article_name,
            article_type,
            level1,
            level2,
            pnl_id,
            old_article_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}