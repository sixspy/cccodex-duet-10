"""对拍统计。"""
from __future__ import annotations
from app.db import 连接

def 左命运() -> dict:
    conn = 连接()
    rows = conn.execute("SELECT 命运, COUNT(*) AS c FROM 左轨流水 GROUP BY 命运").fetchall()
    conn.close()
    return {r["命运"]: r["c"] for r in rows}

def 右命运() -> dict:
    conn = 连接()
    rows = conn.execute("SELECT 命运, COUNT(*) AS c FROM 右轨流水 GROUP BY 命运").fetchall()
    conn.close()
    return {r["命运"]: r["c"] for r in rows}

def 差型分布() -> dict:
    conn = 连接()
    rows = conn.execute("SELECT 差型, COUNT(*) AS c FROM 对齐差 GROUP BY 差型").fetchall()
    conn.close()
    return {r["差型"]: r["c"] for r in rows}

def 悬边分布() -> dict:
    conn = 连接()
    rows = conn.execute("SELECT 悬边, COUNT(*) AS c FROM 悬账单 GROUP BY 悬边").fetchall()
    conn.close()
    return {r["悬边"]: r["c"] for r in rows}

def 换绑分布() -> dict:
    """换绑策略与现行/归档读键形态分布。"""
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT 策略, COUNT(*) AS c FROM 业务键换绑 WHERE valid=1 GROUP BY 策略"
        ).fetchall()
        策略 = {r["策略"]: r["c"] for r in rows}
        归档左 = conn.execute(
            "SELECT COUNT(*) AS c FROM 左轨流水 WHERE 命运='已归档左'"
        ).fetchone()["c"]
        归档右 = conn.execute(
            "SELECT COUNT(*) AS c FROM 右轨流水 WHERE 命运='已归档右'"
        ).fetchone()["c"]
        return {
            "策略": 策略,
            "归档流水": {"左": 归档左, "右": 归档右},
            "生效换绑数": sum(策略.values()),
        }
    finally:
        conn.close()
