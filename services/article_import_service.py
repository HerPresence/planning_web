import os
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials

from db import get_connection

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

GOOGLE_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_CREDENTIALS_PATH",
    r"T:\planning_web\google_credentials.json",
)


def get_google_sheet_rows(sheet_url: str):
    if not sheet_url:
        raise ValueError("Не вказано посилання на Google Sheet")

    if not os.path.exists(GOOGLE_CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"Файл Google credentials не знайдено: {GOOGLE_CREDENTIALS_PATH}"
        )

    credentials = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_PATH,
        scopes=SCOPES,
    )

    gc = gspread.authorize(credentials)

    spreadsheet = gc.open_by_url(sheet_url)

    worksheet = spreadsheet.sheet1

    rows = worksheet.get_all_records()

    return rows


def validate_required_columns(rows, mapping):
    if not rows:
        return []

    available_columns = set(rows[0].keys())

    required_fields = {
        "article_id_field": mapping["article_id_field"],
        "article_name_field": mapping["article_name_field"],
    }

    optional_fields = {
        "article_type_field": mapping.get("article_type_field"),
        "level1_field": mapping.get("level1_field"),
        "level2_field": mapping.get("level2_field"),
        "pnl_id_field": mapping.get("pnl_id_field"),
    }

    missing = []

    for system_field, source_column in required_fields.items():
        if source_column and source_column not in available_columns:
            missing.append(
                {
                    "system_field": system_field,
                    "source_column": source_column,
                }
            )

    for system_field, source_column in optional_fields.items():
        if source_column and source_column not in available_columns:
            missing.append(
                {
                    "system_field": system_field,
                    "source_column": source_column,
                }
            )

    return missing


def safe_text(value):
    if value is None:
        return ""

    return str(value).strip()


def safe_int(value):
    try:
        if value is None or value == "":
            return 0

        return int(float(value))

    except Exception:
        return 0


def import_articles_from_source(source_id: int):
    conn = None
    cur = None

    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                id,
                source_name,
                source_type,
                source_url,
                article_id_field,
                article_name_field,
                article_type_field,
                level1_field,
                level2_field,
                pnl_id_field,
                is_active
            FROM import_sources
            WHERE id = %s
            """,
            (source_id,),
        )

        source = cur.fetchone()

        if not source:
            return {
                "status": "error",
                "message": "Джерело імпорту не знайдено",
                "source_id": source_id,
            }

        (
            source_id_db,
            source_name,
            source_type,
            source_url,
            article_id_field,
            article_name_field,
            article_type_field,
            level1_field,
            level2_field,
            pnl_id_field,
            is_active,
        ) = source

        if not is_active:
            return {
                "status": "error",
                "message": "Джерело імпорту неактивне",
                "source_id": source_id,
                "source_name": source_name,
            }

        if source_type != "google_sheet":
            return {
                "status": "error",
                "message": f"Тип джерела поки не підтримується: {source_type}",
                "source_id": source_id,
                "source_name": source_name,
            }

        mapping = {
            "article_id_field": article_id_field,
            "article_name_field": article_name_field,
            "article_type_field": article_type_field,
            "level1_field": level1_field,
            "level2_field": level2_field,
            "pnl_id_field": pnl_id_field,
        }

        rows = get_google_sheet_rows(source_url)

        if not rows:
            return {
                "status": "error",
                "message": "Google Sheet порожній або не містить рядків з даними",
                "source_id": source_id,
                "source_name": source_name,
            }

        missing_columns = validate_required_columns(rows, mapping)

        if missing_columns:
            return {
                "status": "error",
                "message": "У Google Sheet не знайдено потрібні колонки згідно з відповідністю",
                "source_id": source_id,
                "source_name": source_name,
                "missing_columns": missing_columns,
                "available_columns": list(rows[0].keys()),
            }

        imported = 0
        updated = 0
        skipped = 0
        errors = []

        for index, row in enumerate(rows, start=2):
            try:
                article_id = safe_text(
                    row.get(article_id_field)
                )

                if not article_id:
                    skipped += 1

                    errors.append(
                        {
                            "row": index,
                            "type": "skipped",
                            "message": "Порожній article_id",
                        }
                    )

                    continue

                article_name = safe_text(
                    row.get(article_name_field)
                )

                article_type = safe_text(
                    row.get(article_type_field)
                )

                level1 = safe_text(
                    row.get(level1_field)
                )

                level2 = safe_text(
                    row.get(level2_field)
                )

                pnl_id = safe_int(
                    row.get(pnl_id_field)
                )

                if pnl_id <= 0:
                    skipped += 1

                    errors.append(
                        {
                            "row": index,
                            "type": "skipped",
                            "message": "Некоректний pnl_id або pnl_id відсутній",
                            "article_id": article_id,
                        }
                    )

                    continue

                cur.execute(
                    """
                    SELECT article_id
                    FROM dim_article
                    WHERE article_id = %s
                    """,
                    (article_id,),
                )

                existing = cur.fetchone()

                if existing:
                    cur.execute(
                        """
                        UPDATE dim_article
                        SET
                            article_name = %s,
                            article_type = %s,
                            level1 = %s,
                            level2 = %s,
                            pnl_id = %s,
                            is_active = TRUE
                        WHERE article_id = %s
                        """,
                        (
                            article_name,
                            article_type,
                            level1,
                            level2,
                            pnl_id,
                            article_id,
                        ),
                    )

                    updated += 1

                else:
                    cur.execute(
                        """
                        INSERT INTO dim_article
                        (
                            article_id,
                            article_name,
                            article_type,
                            level1,
                            level2,
                            pnl_id,
                            is_active
                        )
                        VALUES
                        (
                            %s,%s,%s,%s,%s,%s,TRUE
                        )
                        """,
                        (
                            article_id,
                            article_name,
                            article_type,
                            level1,
                            level2,
                            pnl_id,
                        ),
                    )

                    imported += 1

            except Exception as row_error:
                skipped += 1

                errors.append(
                    {
                        "row": index,
                        "type": "error",
                        "message": str(row_error),
                    }
                )

        conn.commit()

        return {
            "status": "ok",
            "message": "Імпорт виконано",
            "source_id": source_id,
            "source_name": source_name,
            "total_rows": len(rows),
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "errors": errors[:50],
        }

    except Exception as e:
        if conn:
            conn.rollback()

        return {
            "status": "error",
            "message": str(e),
            "source_id": source_id,
        }

    finally:
        if cur:
            cur.close()

        if conn:
            conn.close()