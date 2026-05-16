from fastapi import APIRouter, Form, HTTPException
from db import get_connection

router = APIRouter(prefix="/api/pnl")


@router.get("/plan")
def get_plan_pnl():
    """Get all plan PnL records"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT plan_id, period, holding_name, organization_name, region_name, branch_name,
               department_id, department_name, article_id, article_name, pnl_id,
               scenario, version_name, amount, comment, created_at, updated_at
        FROM plan_pnl
        ORDER BY period DESC, holding_name, organization_name
        """
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "plan_id": r[0],
                "period": r[1],
                "holding_name": r[2],
                "organization_name": r[3],
                "region_name": r[4],
                "branch_name": r[5],
                "department_id": r[6],
                "department_name": r[7],
                "article_id": r[8],
                "article_name": r[9],
                "pnl_id": r[10],
                "scenario": r[11],
                "version_name": r[12],
                "amount": r[13],
                "comment": r[14],
                "created_at": r[15],
                "updated_at": r[16],
            }
        )

    cur.close()
    conn.close()

    return result


@router.get("/fact")
def get_fact_pnl():
    """Get all fact PnL records"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT fact_id, period, holding_name, organization_name, region_name, branch_name,
               department_id, department_name, article_id, article_name, pnl_id,
               amount, registrar, source_name, loaded_at
        FROM fact_pnl
        ORDER BY period DESC, holding_name, organization_name
        """
    )
    rows = cur.fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "fact_id": r[0],
                "period": r[1],
                "holding_name": r[2],
                "organization_name": r[3],
                "region_name": r[4],
                "branch_name": r[5],
                "department_id": r[6],
                "department_name": r[7],
                "article_id": r[8],
                "article_name": r[9],
                "pnl_id": r[10],
                "amount": r[11],
                "registrar": r[12],
                "source_name": r[13],
                "loaded_at": r[14],
            }
        )

    cur.close()
    conn.close()

    return result


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
):
    """Create a new plan PnL record"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO plan_pnl
        (period, holding_name, organization_name, region_name, branch_name,
         department_id, department_name, article_id, article_name, pnl_id,
         scenario, version_name, amount, comment, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """,
        (
            period,
            holding_name,
            organization_name,
            region_name,
            branch_name,
            department_id,
            department_name,
            article_id,
            article_name,
            pnl_id,
            scenario,
            version_name,
            amount,
            comment,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


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
):
    """Create a new fact PnL record"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO fact_pnl
        (period, holding_name, organization_name, region_name, branch_name,
         department_id, department_name, article_id, article_name, pnl_id,
         amount, registrar, source_name, loaded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            period,
            holding_name,
            organization_name,
            region_name,
            branch_name,
            department_id,
            department_name,
            article_id,
            article_name,
            pnl_id,
            amount,
            registrar,
            source_name,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


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
):
    """Update a plan PnL record"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE plan_pnl
        SET period = %s,
            holding_name = %s,
            organization_name = %s,
            region_name = %s,
            branch_name = %s,
            department_id = %s,
            department_name = %s,
            article_id = %s,
            article_name = %s,
            pnl_id = %s,
            scenario = %s,
            version_name = %s,
            amount = %s,
            comment = %s,
            updated_at = NOW()
        WHERE plan_id = %s
        """,
        (
            period,
            holding_name,
            organization_name,
            region_name,
            branch_name,
            department_id,
            department_name,
            article_id,
            article_name,
            pnl_id,
            scenario,
            version_name,
            amount,
            comment,
            plan_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


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
):
    """Update a fact PnL record"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE fact_pnl
        SET period = %s,
            holding_name = %s,
            organization_name = %s,
            region_name = %s,
            branch_name = %s,
            department_id = %s,
            department_name = %s,
            article_id = %s,
            article_name = %s,
            pnl_id = %s,
            amount = %s,
            registrar = %s,
            source_name = %s,
            loaded_at = NOW()
        WHERE fact_id = %s
        """,
        (
            period,
            holding_name,
            organization_name,
            region_name,
            branch_name,
            department_id,
            department_name,
            article_id,
            article_name,
            pnl_id,
            amount,
            registrar,
            source_name,
            fact_id,
        ),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/plan/{plan_id}")
def delete_plan_pnl(plan_id: int):
    """Delete (or soft delete) a plan PnL record"""
    conn = get_connection()
    cur = conn.cursor()

    # Перевіряємо, чи існує колона is_active
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name='plan_pnl' AND column_name='is_active'
        """
    )
    has_is_active = cur.fetchone() is not None

    if has_is_active:
        # Soft delete через is_active
        cur.execute(
            """
            UPDATE plan_pnl
            SET is_active = false
            WHERE plan_id = %s
            """,
            (plan_id,),
        )
    else:
        # Таблиця не має is_active - не видаляємо
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="plan_pnl таблиця не підтримує soft delete. Контактуйте адміністратора."
        )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}


@router.delete("/fact/{fact_id}")
def delete_fact_pnl(fact_id: int):
    """Delete (or soft delete) a fact PnL record"""
    conn = get_connection()
    cur = conn.cursor()

    # Перевіряємо, чи існує колона is_active
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name='fact_pnl' AND column_name='is_active'
        """
    )
    has_is_active = cur.fetchone() is not None

    if has_is_active:
        # Soft delete через is_active
        cur.execute(
            """
            UPDATE fact_pnl
            SET is_active = false
            WHERE fact_id = %s
            """,
            (fact_id,),
        )
    else:
        # Таблиця не має is_active - не видаляємо
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="fact_pnl таблиця не підтримує soft delete. Контактуйте адміністратора."
        )

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "ok"}
