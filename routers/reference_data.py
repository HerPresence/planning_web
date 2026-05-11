from fastapi import APIRouter
from db import get_connection

router = APIRouter(prefix="/api/reference", tags=["reference"])


def has_active_flag(table_name):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name='is_active'",
        (table_name,),
    )
    result = cur.fetchone() is not None
    cur.close()
    conn.close()
    return result


def active_filter(table_name):
    if has_active_flag(table_name):
        return "WHERE is_active IS TRUE"
    return ""


@router.get("/departments")
def get_departments():
    conn = get_connection()
    cur = conn.cursor()
    clause = active_filter("dim_department")
    cur.execute(
        f"SELECT department_id, holding_name, organization_name, region_name, branch_name, department_name FROM dim_department {clause} ORDER BY holding_name, organization_name, region_name, branch_name, department_name"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "department_id": r[0],
            "holding_name": r[1],
            "organization_name": r[2],
            "region_name": r[3],
            "branch_name": r[4],
            "department_name": r[5],
        }
        for r in rows
    ]


@router.get("/articles")
def get_articles():
    conn = get_connection()
    cur = conn.cursor()
    clause = active_filter("dim_article")
    cur.execute(
        f"SELECT article_id, article_name, article_type, level1, level2, pnl_id FROM dim_article {clause} ORDER BY article_name"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "article_id": r[0],
            "article_name": r[1],
            "article_type": r[2],
            "level1": r[3],
            "level2": r[4],
            "pnl_id": r[5],
        }
        for r in rows
    ]


@router.get("/sources")
def get_sources():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT source_id, source_name, source_type FROM dim_source WHERE is_active IS TRUE ORDER BY source_name")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {"source_id": r[0], "source_name": r[1], "source_type": r[2]} for r in rows
        ]
    except Exception:
        return []

