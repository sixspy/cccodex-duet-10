"""左/右轨共享装载：换绑契约在此生效。

单批在一个 BEGIN IMMEDIATE 事务内完成，与「业务键换绑登记」互斥：
- 换绑先提交：本批按新读键形态走（归档/拒收）；
- 装载先提交：本批按无绑现行入流，换绑登记随后再统一改读。
不存在两边都成功的中间态，也不存在旧键串进现行双份。
"""
from __future__ import annotations
import json
import uuid
from app.db import 连接
from app.seed import 此刻
from app.services import 校验, 换绑


def 装一批(轨别: str, 表: str, 体: dict) -> dict:
    入流命运 = "已入" + 轨别
    归档命运 = "已归档" + 轨别
    批次 = 体.get("批次编号") or f"{轨别}批-{uuid.uuid4().hex[:8]}"
    事件 = 体.get("事件") or []
    报告 = {
        "批次编号": 批次,
        "轨别": 轨别,
        "入流": 0,
        "归档": 0,
        "跳过": 0,
        "驳回": 0,
        "明细": [],
    }
    for e in 校验.校验批次(体):
        报告["明细"].append({"命运": "批次预检", "说明": e})

    conn = 连接()
    try:
        # 立即取写锁：换绑登记并发时在此排队，读键形态全批一致。
        conn.execute("BEGIN IMMEDIATE")
        映射 = 换绑.生效映射(conn, 轨别)
        for ev in 事件:
            行错 = 校验.校验事件(ev)
            if 行错:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-校验", "说明": ";".join(行错)})
                continue
            编号 = ev.get("事件编号")
            键 = ev.get("业务键")
            if not 编号 or not 键:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-缺字段", "事件": ev})
                continue
            if conn.execute(
                f"SELECT 1 FROM {表} WHERE 事件编号=?", (编号,)
            ).fetchone():
                报告["跳过"] += 1
                报告["明细"].append({"事件编号": 编号, "命运": "幂等跳过"})
                continue
            默认源 = "渠甲" if 轨别 == "左" else "核乙"
            源 = ev.get("源键") or 默认源
            if not conn.execute(
                "SELECT 1 FROM 源登记 WHERE 源键=? AND 轨别=?", (源, 轨别)
            ).fetchone():
                报告["驳回"] += 1
                报告["明细"].append(
                    {"事件编号": 编号, "命运": f"驳回-未知{轨别}源"}
                )
                continue

            绑 = 映射.get(键)
            if 绑 is not None and 绑["策略"] == 换绑.拒收:
                # 拒收形态：旧键回刷一律拒，悬账仍挂旧键，现行不自动迁键。
                报告["驳回"] += 1
                报告["明细"].append(
                    {
                        "事件编号": 编号,
                        "业务键": 键,
                        "命运": "驳回-换绑拒收",
                        "换绑编号": 绑["换绑编号"],
                        "说明": 绑["拒因"] or 换绑.默认拒因,
                    }
                )
                continue

            if 绑 is not None:
                # 归档形态：旧键改读归档，现行对齐集合不收此键。
                读键, 绑号 = 绑["旧键"], 绑["换绑编号"]
                命运 = 归档命运
                报告["归档"] += 1
                报告["明细"].append(
                    {
                        "事件编号": 编号,
                        "业务键": 键,
                        "读键": 读键,
                        "换绑编号": 绑号,
                        "命运": 命运,
                    }
                )
            else:
                读键, 绑号, 命运 = 键, "", 入流命运
                报告["入流"] += 1
                报告["明细"].append(
                    {"事件编号": 编号, "业务键": 键, "读键": 读键, "命运": 命运}
                )

            金额 = int(ev.get("金额分", 0))
            事件时 = ev.get("事件时") or 此刻()
            处理时 = ev.get("处理时") or 此刻()
            conn.execute(
                f"""INSERT INTO {表}
                    (事件编号,业务键,金额分,源键,事件时,处理时,命运,批次编号,读键,换绑编号)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (编号, 键, 金额, 源, 事件时, 处理时, 命运, 批次, 读键, 绑号),
            )
        conn.execute(
            "INSERT INTO 装载批次(编号,轨别,时刻,报告) VALUES (?,?,?,?)",
            (批次, 轨别, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return 报告
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
