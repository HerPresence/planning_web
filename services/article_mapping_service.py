from db import get_connection


def get_all_mappings():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT mapping_id, source_system, source_article_id, source_article_name,
               article_id, comment, is_active
        FROM article_mapping
        ORDER BY mapping_id DESC
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "mapping_id": r[0],
            "source_system": r[1],
            "source_article_id": r[2],
            "source_article_name": r[3],
            "article_id": r[4],
            "comment": r[5],
            "is_active": r[6],
        }
        for r in rows
    ]


def create_mapping(data):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO article_mapping (
            source_system,
            source_article_id,
            source_article_name,
            article_id,
            comment,
            is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING mapping_id
    """, (
        data["source_system"],
        data.get("source_article_id"),
        data["source_article_name"],
        data["article_id"],
        data.get("comment"),
        data.get("is_active", True)
    ))

    mapping_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return mapping_id