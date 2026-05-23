"""
Row Level Security helper.
build_scope_filter(user_id, table_prefix, require_edit) returns (where_sql, params).
Empty string + empty list means "no restriction" (show all rows).
check_write_scope(user_id, field_values) returns True if write is allowed.
"""
from db import get_connection

_SCOPE_COL = {
    "holding":      "holding_name",
    "organization": "organization_name",
    "region":       "region_name",
    "branch":       "branch_name",
    "department":   "department_name",
}

VALID_SCOPE_TYPES = set(_SCOPE_COL.keys())


def build_scope_filter(user_id: int, table_prefix: str = "", require_edit: bool = False) -> tuple:
    """
    Returns (sql_fragment, params).
    sql_fragment is empty string when user has no restrictions.
    table_prefix adds "alias." before column names when needed.
    When require_edit=True, only rules with can_edit=TRUE are included.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        if require_edit:
            cur.execute(
                "SELECT scope_type, scope_value FROM user_data_scope WHERE user_id = %s AND can_edit = TRUE",
                (user_id,)
            )
        else:
            cur.execute(
                "SELECT scope_type, scope_value FROM user_data_scope WHERE user_id = %s",
                (user_id,)
            )
        rules = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not rules:
        return "", []

    p = (table_prefix + ".") if table_prefix else ""
    conditions = []
    params = []
    for scope_type, scope_value in rules:
        col = _SCOPE_COL.get(scope_type)
        if col:
            conditions.append(f"{p}{col} = %s")
            params.append(scope_value)

    if not conditions:
        return "", []

    return "(" + " OR ".join(conditions) + ")", params


def check_write_scope(user_id: int, field_values: dict) -> bool:
    """
    Returns True if the user is allowed to write to the given dimensional context.
    If user has no can_edit scope rules, write access is unrestricted (returns True).
    field_values: e.g. {"holding_name": "ABC", "organization_name": "XYZ", ...}
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT scope_type, scope_value FROM user_data_scope WHERE user_id = %s AND can_edit = TRUE",
            (user_id,)
        )
        write_rules = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not write_rules:
        return True  # no write-scope restrictions

    for scope_type, scope_value in write_rules:
        col = _SCOPE_COL.get(scope_type)
        if col and field_values.get(col) == scope_value:
            return True

    return False
