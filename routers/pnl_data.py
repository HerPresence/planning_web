import io
import csv
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import StreamingResponse
from auth.dependencies import get_current_user
from db import get_connection
from services.rls_service import build_scope_filter, check_write_scope

router = APIRouter(prefix="/api/pnl")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_where(user, period_from, period_to, holding_name, organization_name,
                 region_name, branch_name, department_name, article_name, pnl_id,
                 search, extra_filters=None, search_extra_cols=None):
    """Returns (where_sql, params). RLS scope is applied first."""
    where_parts = []
    params = []

    # 1. RLS scope — before any user filter
    if not user["is_admin"]:
        scope_sql, scope_params = build_scope_filter(user["id"])
        if scope_sql:
            where_parts.append(scope_sql)
            params.extend(scope_params)

    # 2. Period range (YYYY-MM or YYYY-MM-DD)
    if period_from:
        pf = period_from + "-01" if len(period_from) == 7 else period_from
        where_parts.append("CAST(period AS TEXT) >= %s")
        params.append(pf)
    if period_to:
        if len(period_to) == 7:
            y, m = int(period_to[:4]), int(period_to[5:7])
            m += 1
            if m > 12:
                m, y = 1, y + 1
            where_parts.append("CAST(period AS TEXT) < %s")
            params.append(f"{y:04d}-{m:02d}-01")
        else:
            where_parts.append("CAST(period AS TEXT) <= %s")
            params.append(period_to)

    # 3. Dimension exact-match filters
    for col, val in [
        ("holding_name",      holding_name),
        ("organization_name", organization_name),
        ("region_name",       region_name),
        ("branch_name",       branch_name),
        ("department_name",   department_name),
        ("article_name",      article_name),
        ("pnl_id",            pnl_id),
    ]:
        if val:
            where_parts.append(f"{col} = %s")
            params.append(val)

    # 4. Tab-specific filters (scenario, version_name / registrar, source_name)
    if extra_filters:
        for col, val in extra_filters:
            if val:
                where_parts.append(f"{col} = %s")
                params.append(val)

    # 5. Free-text search
    if search:
        base_cols = ["article_name", "department_name", "CAST(pnl_id AS TEXT)",
                     "holding_name", "organization_name"]
        all_cols = base_cols + (search_extra_cols or [])
        ilike_clause = " OR ".join(f"{col} ILIKE %s" for col in all_cols)
        where_parts.append(f"({ilike_clause})")
        s = f"%{search}%"
        params.extend([s] * len(all_cols))

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    return where_sql, params


# ── GET /plan ──────────────────────────────────────────────────────────────────

@router.get("/plan")
def get_plan_pnl(
    period_from:       Optional[str] = Query(None),
    period_to:         Optional[str] = Query(None),
    holding_name:      Optional[str] = Query(None),
    organization_name: Optional[str] = Query(None),
    region_name:       Optional[str] = Query(None),
    branch_name:       Optional[str] = Query(None),
    department_name:   Optional[str] = Query(None),
    article_name:      Optional[str] = Query(None),
    pnl_id:            Optional[str] = Query(None),
    scenario:          Optional[str] = Query(None),
    version_name:      Optional[str] = Query(None),
    search:            Optional[str] = Query(None),
    page:              int           = Query(1, ge=1),
    page_size:         int           = Query(50, ge=1, le=1000),
    user=Depends(get_current_user),
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_where(
            user, period_from, period_to, holding_name, organization_name,
            region_name, branch_name, department_name, article_name, pnl_id, search,
            extra_filters=[("scenario", scenario), ("version_name", version_name)],
        )

        cur.execute(
            f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM plan_pnl {where_sql}",
            params,
        )
        total_count, total_amount = cur.fetchone()

        offset = (page - 1) * page_size
        cur.execute(
            f"""SELECT plan_id, period, holding_name, organization_name, region_name, branch_name,
                       department_id, department_name, article_id, article_name, pnl_id,
                       scenario, version_name, amount, comment, created_at, updated_at
                FROM plan_pnl {where_sql}
                ORDER BY period DESC, holding_name, organization_name, department_name
                LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        rows = cur.fetchall()

        return {
            "total_count": int(total_count),
            "total_amount": float(total_amount),
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "plan_id": r[0], "period": str(r[1]) if r[1] else "",
                    "holding_name": r[2], "organization_name": r[3],
                    "region_name": r[4], "branch_name": r[5],
                    "department_id": r[6], "department_name": r[7],
                    "article_id": r[8], "article_name": r[9], "pnl_id": r[10],
                    "scenario": r[11], "version_name": r[12],
                    "amount": r[13], "comment": r[14],
                    "created_at": str(r[15]) if r[15] else None,
                    "updated_at": str(r[16]) if r[16] else None,
                }
                for r in rows
            ],
        }
    finally:
        cur.close()
        conn.close()


# ── GET /fact ──────────────────────────────────────────────────────────────────

@router.get("/fact")
def get_fact_pnl(
    period_from:       Optional[str] = Query(None),
    period_to:         Optional[str] = Query(None),
    holding_name:      Optional[str] = Query(None),
    organization_name: Optional[str] = Query(None),
    region_name:       Optional[str] = Query(None),
    branch_name:       Optional[str] = Query(None),
    department_name:   Optional[str] = Query(None),
    article_name:      Optional[str] = Query(None),
    pnl_id:            Optional[str] = Query(None),
    registrar:         Optional[str] = Query(None),
    source_name:       Optional[str] = Query(None),
    search:            Optional[str] = Query(None),
    page:              int           = Query(1, ge=1),
    page_size:         int           = Query(50, ge=1, le=1000),
    user=Depends(get_current_user),
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_where(
            user, period_from, period_to, holding_name, organization_name,
            region_name, branch_name, department_name, article_name, pnl_id, search,
            extra_filters=[("registrar", registrar), ("source_name", source_name)],
            search_extra_cols=["registrar", "source_name"],
        )

        cur.execute(
            f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM fact_pnl {where_sql}",
            params,
        )
        total_count, total_amount = cur.fetchone()

        offset = (page - 1) * page_size
        cur.execute(
            f"""SELECT fact_id, period, holding_name, organization_name, region_name, branch_name,
                       department_id, department_name, article_id, article_name, pnl_id,
                       amount, registrar, source_name, loaded_at
                FROM fact_pnl {where_sql}
                ORDER BY period DESC, holding_name, organization_name, department_name
                LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        rows = cur.fetchall()

        return {
            "total_count": int(total_count),
            "total_amount": float(total_amount),
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "fact_id": r[0], "period": str(r[1]) if r[1] else "",
                    "holding_name": r[2], "organization_name": r[3],
                    "region_name": r[4], "branch_name": r[5],
                    "department_id": r[6], "department_name": r[7],
                    "article_id": r[8], "article_name": r[9], "pnl_id": r[10],
                    "amount": r[11], "registrar": r[12], "source_name": r[13],
                    "loaded_at": str(r[14]) if r[14] else None,
                }
                for r in rows
            ],
        }
    finally:
        cur.close()
        conn.close()


# ── GET /export/plan ──────────────────────────────────────────────────────────

@router.get("/export/plan")
def export_plan_pnl(
    period_from:       Optional[str] = Query(None),
    period_to:         Optional[str] = Query(None),
    holding_name:      Optional[str] = Query(None),
    organization_name: Optional[str] = Query(None),
    region_name:       Optional[str] = Query(None),
    branch_name:       Optional[str] = Query(None),
    department_name:   Optional[str] = Query(None),
    article_name:      Optional[str] = Query(None),
    pnl_id:            Optional[str] = Query(None),
    scenario:          Optional[str] = Query(None),
    version_name:      Optional[str] = Query(None),
    search:            Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_where(
            user, period_from, period_to, holding_name, organization_name,
            region_name, branch_name, department_name, article_name, pnl_id, search,
            extra_filters=[("scenario", scenario), ("version_name", version_name)],
        )
        cur.execute(
            f"""SELECT plan_id, period, holding_name, organization_name, region_name, branch_name,
                       department_id, department_name, article_id, article_name, pnl_id,
                       scenario, version_name, amount, comment, created_at, updated_at
                FROM plan_pnl {where_sql}
                ORDER BY period DESC, holding_name, organization_name, department_name
                LIMIT 10000""",
            params,
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Період","Холдинг","Організація","Регіон","Філія",
                     "Відділ ID","Підрозділ","Стаття ID","Стаття","PnL",
                     "Сценарій","Версія","Сума","Коментар","Створено","Оновлено"])
    for r in rows:
        writer.writerow([str(c) if c is not None else "" for c in r])
    csv_bytes = output.getvalue().encode("utf-8-sig")

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pnl_plan.csv"'},
    )


# ── GET /export/fact ───────────────────────────────────────────────────────────

@router.get("/export/fact")
def export_fact_pnl(
    period_from:       Optional[str] = Query(None),
    period_to:         Optional[str] = Query(None),
    holding_name:      Optional[str] = Query(None),
    organization_name: Optional[str] = Query(None),
    region_name:       Optional[str] = Query(None),
    branch_name:       Optional[str] = Query(None),
    department_name:   Optional[str] = Query(None),
    article_name:      Optional[str] = Query(None),
    pnl_id:            Optional[str] = Query(None),
    registrar:         Optional[str] = Query(None),
    source_name:       Optional[str] = Query(None),
    search:            Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    conn = get_connection()
    cur = conn.cursor()
    try:
        where_sql, params = _build_where(
            user, period_from, period_to, holding_name, organization_name,
            region_name, branch_name, department_name, article_name, pnl_id, search,
            extra_filters=[("registrar", registrar), ("source_name", source_name)],
            search_extra_cols=["registrar", "source_name"],
        )
        cur.execute(
            f"""SELECT fact_id, period, holding_name, organization_name, region_name, branch_name,
                       department_id, department_name, article_id, article_name, pnl_id,
                       amount, registrar, source_name, loaded_at
                FROM fact_pnl {where_sql}
                ORDER BY period DESC, holding_name, organization_name, department_name
                LIMIT 10000""",
            params,
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID","Період","Холдинг","Організація","Регіон","Філія",
                     "Відділ ID","Підрозділ","Стаття ID","Стаття","PnL",
                     "Сума","Реєстратор","Джерело","Завантажено"])
    for r in rows:
        writer.writerow([str(c) if c is not None else "" for c in r])
    csv_bytes = output.getvalue().encode("utf-8-sig")

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pnl_fact.csv"'},
    )


# ── POST /plan ─────────────────────────────────────────────────────────────────

@router.post("/plan")
def create_plan_pnl(
    period: str = Form(...),
    holding_name: str = Form(""),
    organization_name: str = Form(""),
    region_name: str = Form(""),
    branch_name: str = Form(""),
    department_id: str = Form(""),
    department_name: str = Form(""),
    article_id: str = Form(...),
    article_name: str = Form(""),
    pnl_id: str = Form(""),
    scenario: str = Form(""),
    version_name: str = Form(""),
    amount: float = Form(...),
    comment: str = Form(""),
    user=Depends(get_current_user),
):
    if not user["is_admin"] and not check_write_scope(user["id"], {
        "holding_name": holding_name, "organization_name": organization_name,
        "region_name": region_name, "branch_name": branch_name, "department_name": department_name,
    }):
        raise HTTPException(403, "У вас немає прав для редагування даних цього підрозділу")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO plan_pnl
               (period, holding_name, organization_name, region_name, branch_name,
                department_id, department_name, article_id, article_name, pnl_id,
                scenario, version_name, amount, comment, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())""",
            (period, holding_name, organization_name, region_name, branch_name,
             department_id, department_name, article_id, article_name, pnl_id,
             scenario, version_name, amount, comment),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()


# ── POST /fact ─────────────────────────────────────────────────────────────────

@router.post("/fact")
def create_fact_pnl(
    period: str = Form(...),
    holding_name: str = Form(""),
    organization_name: str = Form(""),
    region_name: str = Form(""),
    branch_name: str = Form(""),
    department_id: str = Form(""),
    department_name: str = Form(""),
    article_id: str = Form(...),
    article_name: str = Form(""),
    pnl_id: str = Form(""),
    amount: float = Form(...),
    registrar: str = Form(""),
    source_name: str = Form(""),
    user=Depends(get_current_user),
):
    if not user["is_admin"] and not check_write_scope(user["id"], {
        "holding_name": holding_name, "organization_name": organization_name,
        "region_name": region_name, "branch_name": branch_name, "department_name": department_name,
    }):
        raise HTTPException(403, "У вас немає прав для редагування даних цього підрозділу")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO fact_pnl
               (period, holding_name, organization_name, region_name, branch_name,
                department_id, department_name, article_id, article_name, pnl_id,
                amount, registrar, source_name, loaded_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",
            (period, holding_name, organization_name, region_name, branch_name,
             department_id, department_name, article_id, article_name, pnl_id,
             amount, registrar, source_name),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()


# ── PUT /plan/{plan_id} ────────────────────────────────────────────────────────

@router.put("/plan/{plan_id}")
def update_plan_pnl(
    plan_id: int,
    period: str = Form(...),
    holding_name: str = Form(""),
    organization_name: str = Form(""),
    region_name: str = Form(""),
    branch_name: str = Form(""),
    department_id: str = Form(""),
    department_name: str = Form(""),
    article_id: str = Form(...),
    article_name: str = Form(""),
    pnl_id: str = Form(""),
    scenario: str = Form(""),
    version_name: str = Form(""),
    amount: float = Form(...),
    comment: str = Form(""),
    user=Depends(get_current_user),
):
    if not user["is_admin"] and not check_write_scope(user["id"], {
        "holding_name": holding_name, "organization_name": organization_name,
        "region_name": region_name, "branch_name": branch_name, "department_name": department_name,
    }):
        raise HTTPException(403, "У вас немає прав для редагування даних цього підрозділу")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE plan_pnl SET
               period=%s, holding_name=%s, organization_name=%s, region_name=%s, branch_name=%s,
               department_id=%s, department_name=%s, article_id=%s, article_name=%s, pnl_id=%s,
               scenario=%s, version_name=%s, amount=%s, comment=%s, updated_at=NOW()
               WHERE plan_id=%s""",
            (period, holding_name, organization_name, region_name, branch_name,
             department_id, department_name, article_id, article_name, pnl_id,
             scenario, version_name, amount, comment, plan_id),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()


# ── PUT /fact/{fact_id} ────────────────────────────────────────────────────────

@router.put("/fact/{fact_id}")
def update_fact_pnl(
    fact_id: int,
    period: str = Form(...),
    holding_name: str = Form(""),
    organization_name: str = Form(""),
    region_name: str = Form(""),
    branch_name: str = Form(""),
    department_id: str = Form(""),
    department_name: str = Form(""),
    article_id: str = Form(...),
    article_name: str = Form(""),
    pnl_id: str = Form(""),
    amount: float = Form(...),
    registrar: str = Form(""),
    source_name: str = Form(""),
    user=Depends(get_current_user),
):
    if not user["is_admin"] and not check_write_scope(user["id"], {
        "holding_name": holding_name, "organization_name": organization_name,
        "region_name": region_name, "branch_name": branch_name, "department_name": department_name,
    }):
        raise HTTPException(403, "У вас немає прав для редагування даних цього підрозділу")
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE fact_pnl SET
               period=%s, holding_name=%s, organization_name=%s, region_name=%s, branch_name=%s,
               department_id=%s, department_name=%s, article_id=%s, article_name=%s, pnl_id=%s,
               amount=%s, registrar=%s, source_name=%s, loaded_at=NOW()
               WHERE fact_id=%s""",
            (period, holding_name, organization_name, region_name, branch_name,
             department_id, department_name, article_id, article_name, pnl_id,
             amount, registrar, source_name, fact_id),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()


# ── DELETE /plan/{plan_id} ─────────────────────────────────────────────────────

@router.delete("/plan/{plan_id}")
def delete_plan_pnl(plan_id: int, user=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if not user["is_admin"]:
            cur.execute(
                "SELECT holding_name, organization_name, region_name, branch_name, department_name FROM plan_pnl WHERE plan_id = %s",
                (plan_id,),
            )
            row = cur.fetchone()
            if row and not check_write_scope(user["id"], {
                "holding_name": row[0], "organization_name": row[1],
                "region_name": row[2], "branch_name": row[3], "department_name": row[4],
            }):
                raise HTTPException(403, "У вас немає прав для редагування даних цього підрозділу")

        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name='plan_pnl' AND column_name='is_active'"
        )
        if cur.fetchone():
            cur.execute("UPDATE plan_pnl SET is_active = FALSE WHERE plan_id = %s", (plan_id,))
        else:
            raise HTTPException(400, "plan_pnl не підтримує soft delete")
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()


# ── DELETE /fact/{fact_id} ─────────────────────────────────────────────────────

@router.delete("/fact/{fact_id}")
def delete_fact_pnl(fact_id: int, user=Depends(get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if not user["is_admin"]:
            cur.execute(
                "SELECT holding_name, organization_name, region_name, branch_name, department_name FROM fact_pnl WHERE fact_id = %s",
                (fact_id,),
            )
            row = cur.fetchone()
            if row and not check_write_scope(user["id"], {
                "holding_name": row[0], "organization_name": row[1],
                "region_name": row[2], "branch_name": row[3], "department_name": row[4],
            }):
                raise HTTPException(403, "У вас немає прав для редагування даних цього підрозділу")

        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name='fact_pnl' AND column_name='is_active'"
        )
        if cur.fetchone():
            cur.execute("UPDATE fact_pnl SET is_active = FALSE WHERE fact_id = %s", (fact_id,))
        else:
            raise HTTPException(400, "fact_pnl не підтримує soft delete")
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()
