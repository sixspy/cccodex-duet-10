"""业务键换绑：渠道改键后的读键形态登记与解析。

两种策略（登记时择定，载入与对齐都按此读键）：
- 归档：旧键进只读归档。旧键悬账与旧键流水改读归档（现行读键=旧键），
  现行对齐只读新键；新旧读引用不同，互不相串。
- 拒收：切流账册拒旧键回刷。旧键装载一律拒收并带拒因，悬账仍挂旧键直至人工迁，
  现行不自动迁键（现行读键=新键）。

并发口径：登记与装载都在 BEGIN IMMEDIATE 单事务内完成，SQLite 写锁串行化，
要么都按换绑前、要么都按换绑后，不允许双成功/串读。
"""
from __future__ import annotations
import uuid
from app.db import 连接
from app.seed import 此刻

归档 = "归档"
拒收 = "拒收"
策略集 = (归档, 拒收)
默认拒因 = "换绑切流：旧键回刷拒收，悬账挂旧键待人工迁"


def _校验(体: dict) -> tuple[str, str, str, str, str, str]:
    if not isinstance(体, dict):
        raise ValueError("体须为对象")
    轨别 = str(体.get("轨别") or "").strip()
    if 轨别 not in ("左", "右"):
        raise ValueError("轨别须为 左 或 右")
    旧键 = str(体.get("旧键") or "").strip()
    新键 = str(体.get("新键") or "").strip()
    for 名, v in (("旧键", 旧键), ("新键", 新键)):
        if not v:
            raise ValueError(f"缺字段:{名}")
        if " " in v or "\t" in v:
            raise ValueError(f"{名}含空白")
        if len(v) > 128:
            raise ValueError(f"{名}过长")
    if 旧键 == 新键:
        raise ValueError("旧键与新键相同，不构成换绑")
    策略 = str(体.get("策略") or "").strip()
    if 策略 not in 策略集:
        raise ValueError(f"策略须为 {归档} 或 {拒收}")
    源键 = str(体.get("源键") or "").strip()
    拒因 = str(体.get("拒因") or "").strip()
    if 策略 == 拒收 and not 拒因:
        拒因 = 默认拒因
    return 轨别, 源键, 旧键, 新键, 策略, 拒因


def 登记(体: dict) -> dict:
    轨别, 源键, 旧键, 新键, 策略, 拒因 = _校验(体)
    # 归档：现行对齐只读新键；拒收：现行读键也是新键，旧键根本不许再装。
    现行读键 = 旧键 if 策略 == 归档 else 新键
    换绑编号 = f"RB-{uuid.uuid4().hex[:10]}"
    时刻 = 此刻()
    conn = 连接()
    try:
        # 立即拿写锁：与并发装载互斥，杜绝「不加锁双成功」。
        conn.execute("BEGIN IMMEDIATE")
        if 源键:
            在 = conn.execute(
                "SELECT 1 FROM 源登记 WHERE 源键=? AND 轨别=?", (源键, 轨别)
            ).fetchone()
            if not 在:
                raise ValueError(f"未知{轨别}源:{源键}")
        撞 = conn.execute(
            "SELECT 换绑编号 FROM 业务键换绑 WHERE 轨别=? AND 旧键=? AND valid=1",
            (轨别, 旧键),
        ).fetchone()
        if 撞:
            raise ValueError(f"{轨别}轨旧键 {旧键} 已有生效换绑 {撞['换绑编号']}")
        conn.execute(
            """INSERT INTO 业务键换绑
               (换绑编号,轨别,源键,旧键,新键,现行读键,策略,拒因,valid,时刻)
               VALUES (?,?,?,?,?,?,?,?,1,?)""",
            (换绑编号, 轨别, 源键, 旧键, 新键, 现行读键, 策略, 拒因, 时刻),
        )
        if 策略 == 归档:
            # 钉死旧键命运：换绑前已入流的旧键流水同步改读归档，
            # 与换绑后旧键再装走同一归档命运，对齐读键同成败。
            表 = "左轨流水" if 轨别 == "左" else "右轨流水"
            命运 = "已归档左" if 轨别 == "左" else "已归档右"
            cur = conn.execute(
                f"""UPDATE {表}
                    SET 命运=?, 读键=?, 换绑编号=?
                    WHERE 业务键=? AND 命运=? AND (换绑编号='' OR 换绑编号 IS NULL)""",
                (命运, 旧键, 换绑编号, 旧键, "已入" + 轨别),
            )
            归档笔数 = cur.rowcount
        else:
            归档笔数 = 0
        conn.commit()
        行 = _取一行(conn, 轨别, 旧键)
        行["随绑归档笔数"] = 归档笔数 if 策略 == 归档 else 0
        return 行
    finally:
        conn.close()


def _取一行(conn, 轨别: str, 旧键: str) -> dict:
    r = conn.execute(
        "SELECT * FROM 业务键换绑 WHERE 轨别=? AND 旧键=? ORDER BY 时刻 DESC",
        (轨别, 旧键),
    ).fetchone()
    return dict(r) if r else {}


def 详情(轨别: str, 旧键: str) -> dict:
    conn = 连接()
    try:
        行 = _取一行(conn, 轨别, 旧键)
        if not 行:
            raise KeyError(旧键)
        return 行
    finally:
        conn.close()


def 列表() -> list[dict]:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT * FROM 业务键换绑 ORDER BY 轨别, 时刻 DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def 生效映射(conn, 轨别: str) -> dict:
    """装载事务内调用：取该轨全部生效换绑，按旧键索引。"""
    return {
        r["旧键"]: r
        for r in conn.execute(
            "SELECT * FROM 业务键换绑 WHERE 轨别=? AND valid=1", (轨别,)
        ).fetchall()
    }
