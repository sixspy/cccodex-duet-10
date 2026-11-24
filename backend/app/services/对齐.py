"""同读键对齐：双边金额比对，落对齐差 + 悬账单。

换绑后键分两套读引用，互不相串：
- 现行集合：命运=已入左/已入右，按「读键」汇总，现行对齐只读新键；
- 归档集合：命运=已归档左/已归档右（旧键），只读悬置，永不与现行同键对齐。
拒收形态下旧键装载被驳回、无流水落库，其旧键悬账仍挂旧键（重算时继续保留）。
"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.services import 目录


def _汇总(conn, 表: str, 命运: str) -> tuple[dict[str, int], dict[str, str], dict[str, str]]:
    汇总: dict[str, int] = {}
    代表: dict[str, str] = {}
    绑号: dict[str, str] = {}
    for r in conn.execute(
        f"SELECT * FROM {表} WHERE 命运=?", (命运,)
    ).fetchall():
        k = r["读键"] or r["业务键"]
        汇总[k] = 汇总.get(k, 0) + int(r["金额分"])
        代表[k] = r["事件编号"]
        if r["换绑编号"]:
            绑号[k] = r["换绑编号"]
    return 汇总, 代表, 绑号


def 跑对齐() -> dict:
    规 = 目录.轨规()
    容差 = int(规.get("金额容差分", "0"))
    conn = 连接()
    try:
        # 基线默认：每次重算对齐结果与悬账。
        conn.execute("DELETE FROM 对齐差")
        conn.execute("DELETE FROM 悬账单")
        时刻 = 此刻()

        # —— 现行集合（新键世界）——
        左汇总, 左代表, _ = _汇总(conn, "左轨流水", "已入左")
        右汇总, 右代表, _ = _汇总(conn, "右轨流水", "已入右")
        现行键 = set(左汇总) | set(右汇总)
        对齐 = 差 = 悬 = 0

        for k in sorted(现行键):
            有左, 有右 = k in 左汇总, k in 右汇总
            if 有左 and 有右:
                la, ra = 左汇总[k], 右汇总[k]
                if abs(la - ra) <= 容差:
                    conn.execute(
                        "INSERT INTO 对齐差(业务键,读键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?,?)",
                        (k, k, la, ra, "对齐", "双边金额在容差内", 时刻),
                    )
                    对齐 += 1
                else:
                    conn.execute(
                        "INSERT INTO 对齐差(业务键,读键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?,?)",
                        (k, k, la, ra, "金额不符", f"左{la}-右{ra}", 时刻),
                    )
                    差 += 1
            elif 有左:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,读键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?,?)",
                    (k, k, 左汇总[k], None, "右缺", "仅左轨有键", 时刻),
                )
                conn.execute(
                    """INSERT INTO 悬账单(业务键,读键,原业务键,悬边,金额分,事件编号,原因,时刻)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (k, k, k, "左", 左汇总[k], 左代表.get(k, ""), "右轨缺键", 时刻),
                )
                悬 += 1
                差 += 1
            else:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,读键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?,?)",
                    (k, k, None, 右汇总[k], "左缺", "仅右轨有键", 时刻),
                )
                conn.execute(
                    """INSERT INTO 悬账单(业务键,读键,原业务键,悬边,金额分,事件编号,原因,时刻)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (k, k, k, "右", 右汇总[k], 右代表.get(k, ""), "左轨缺键", 时刻),
                )
                悬 += 1
                差 += 1

        # —— 归档集合（旧键世界，只读悬置，不进现行对齐）——
        左归, 左归代, 左绑 = _汇总(conn, "左轨流水", "已归档左")
        右归, 右归代, 右绑 = _汇总(conn, "右轨流水", "已归档右")
        归档键 = set(左归) | set(右归)
        归档悬 = 0
        for k in sorted(归档键):
            la = 左归.get(k)
            ra = 右归.get(k)
            绑号 = 左绑.get(k) or 右绑.get(k) or ""
            边 = []
            if la is not None:
                边.append(f"左{la}")
            if ra is not None:
                边.append(f"右{ra}")
            conn.execute(
                "INSERT INTO 对齐差(业务键,读键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?,?)",
                (
                    k, k, la, ra, "归档悬置",
                    f"旧键换绑只读归档({'/'.join(边)})，不入现行对齐 {绑号}".rstrip(),
                    时刻,
                ),
            )
            差 += 1
            if la is not None:
                conn.execute(
                    """INSERT INTO 悬账单(业务键,读键,原业务键,悬边,金额分,事件编号,原因,时刻)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (k, k, k, "左", la, 左归代.get(k, ""),
                     f"旧键换绑归档·只读不入现行 {绑号}".rstrip(), 时刻),
                )
                悬 += 1
                归档悬 += 1
            if ra is not None:
                conn.execute(
                    """INSERT INTO 悬账单(业务键,读键,原业务键,悬边,金额分,事件编号,原因,时刻)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (k, k, k, "右", ra, 右归代.get(k, ""),
                     f"旧键换绑归档·只读不入现行 {绑号}".rstrip(), 时刻),
                )
                悬 += 1
                归档悬 += 1

        conn.commit()
        return {
            "现行键总数": len(现行键),
            "归档键总数": len(归档键),
            "对齐": 对齐,
            "差与悬": 差,
            "悬账": 悬,
            "归档悬账": 归档悬,
            "容差分": 容差,
            "时刻": 时刻,
        }
    finally:
        conn.close()
