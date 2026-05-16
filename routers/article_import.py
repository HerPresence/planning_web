from fastapi import APIRouter, Body
from pydantic import BaseModel
from typing import Optional

from db import get_connection
from services.article_import_service import (
    preview_articles_source,
    import_articles_source,
    ensure_article_columns,
    ensure_source_staging_tables,
)

router = APIRouter(prefix="/api/import-articles")

# ── source loader ─────────────────────────────────────────────────────────────

_SOURCE_SQL = """
    SELECT source_name, source_type, db_server, db_port, db_database,
           db_login, db_password, db_cube_model, db_query, source_url,
           article_id_field, article_name_field, article_type_field,
           level1_field, level2_field, pnl_id_field,
           uid_expense_article_field, expense_element_field, expense_company_field,
           level2_olap_field, level1_olap_field
    FROM import_sources
    WHERE id = %s AND is_active = TRUE
"""


def _load_source(source_id: int) -> dict | None:
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SOURCE_SQL, (source_id,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        return None

    return {
        "source_id":                  source_id,
        "source_name":                row[0],
        "source_type":                row[1],
        "db_server":                  row[2]  or "",
        "db_port":                    row[3]  or "",
        "db_database":                row[4]  or "",
        "db_login":                   row[5]  or "",
        "db_password":                row[6]  or "",
        "db_cube_model":              row[7]  or "",
        "db_query":                   row[8]  or "",
        "source_url":                 row[9]  or "",
        # base mapping fields
        "article_id_field":           row[10] or "",
        "article_name_field":         row[11] or "",
        "article_type_field":         row[12] or "",
        "level1_field":               row[13] or "",
        "level2_field":               row[14] or "",
        "pnl_id_field":               row[15] or "",
        # new OLAP-extended mapping fields
        "uid_expense_article_field":  row[16] or "",
        "expense_element_field":      row[17] or "",
        "expense_company_field":      row[18] or "",
        "level2_olap_field":          row[19] or "",
        "level1_olap_field":          row[20] or "",
    }


# ── request model ─────────────────────────────────────────────────────────────

class ArticleMapping(BaseModel):
    # base fields
    article_id_col:          str            = ""
    article_name_col:        str            = ""
    article_type_col:        Optional[str]  = ""
    level1_col:              Optional[str]  = ""
    level2_col:              Optional[str]  = ""
    pnl_id_col:              Optional[str]  = ""
    # extended OLAP fields
    uid_expense_article_col: Optional[str]  = ""
    expense_element_col:     Optional[str]  = ""
    expense_company_col:     Optional[str]  = ""
    level2_olap_col:         Optional[str]  = ""
    level1_olap_col:         Optional[str]  = ""


def _mapping_to_dict(m: ArticleMapping) -> dict:
    return {
        "article_id_col":          m.article_id_col,
        "article_name_col":        m.article_name_col,
        "article_type_col":        m.article_type_col        or "",
        "level1_col":              m.level1_col              or "",
        "level2_col":              m.level2_col              or "",
        "pnl_id_col":              m.pnl_id_col              or "",
        "uid_expense_article_col": m.uid_expense_article_col or "",
        "expense_element_col":     m.expense_element_col     or "",
        "expense_company_col":     m.expense_company_col     or "",
        "level2_olap_col":         m.level2_olap_col         or "",
        "level1_olap_col":         m.level1_olap_col         or "",
    }


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/{source_id}/preview")
def preview_articles(source_id: int):
    """Return first 10 raw rows + columns for the mapping UI."""
    ensure_article_columns()
    ensure_source_staging_tables()
    source = _load_source(source_id)
    if not source:
        return {"status": "error", "message": "Джерело не знайдено або неактивне"}

    result = preview_articles_source(source)

    # Saved mapping: auto-fill dropdowns in the frontend
    result["saved_mapping"] = {
        "article_id_col":          source["article_id_field"],
        "article_name_col":        source["article_name_field"],
        "article_type_col":        source["article_type_field"],
        "level1_col":              source["level1_field"],
        "level2_col":              source["level2_field"],
        "pnl_id_col":              source["pnl_id_field"],
        "uid_expense_article_col": source["uid_expense_article_field"],
        "expense_element_col":     source["expense_element_field"],
        "expense_company_col":     source["expense_company_field"],
        "level2_olap_col":         source["level2_olap_field"],
        "level1_olap_col":         source["level1_olap_field"],
    }

    return result


@router.post("/{source_id}/save-mapping")
def save_article_mapping(source_id: int, mapping: ArticleMapping):
    """Persist full column mapping to import_sources."""
    ensure_article_columns()
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """UPDATE import_sources
               SET article_id_field           = %s,
                   article_name_field          = %s,
                   article_type_field          = %s,
                   level1_field                = %s,
                   level2_field                = %s,
                   pnl_id_field                = %s,
                   uid_expense_article_field   = %s,
                   expense_element_field       = %s,
                   expense_company_field       = %s,
                   level2_olap_field            = %s,
                   level1_olap_field            = %s
               WHERE id = %s""",
            (
                mapping.article_id_col,
                mapping.article_name_col,
                mapping.article_type_col        or "",
                mapping.level1_col              or "",
                mapping.level2_col              or "",
                mapping.pnl_id_col              or "",
                mapping.uid_expense_article_col or "",
                mapping.expense_element_col     or "",
                mapping.expense_company_col     or "",
                mapping.level2_olap_col         or "",
                mapping.level1_olap_col         or "",
                source_id,
            ),
        )
        conn.commit()
        return {"status": "ok", "message": "Маппінг збережено"}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        cur.close()
        conn.close()


@router.post("/{source_id}")
def import_articles(source_id: int, mapping: ArticleMapping = Body(...)):
    """Import all rows from source using given column mapping."""
    ensure_article_columns()
    source = _load_source(source_id)
    if not source:
        return {"status": "error", "message": "Джерело не знайдено або неактивне"}

    if not mapping.article_id_col:
        return {"status": "error", "message": "Не вказана колонка для ID статті (article_id_col)"}

    return import_articles_source(source, _mapping_to_dict(mapping))
