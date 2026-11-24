from datetime import datetime, timezone
from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 轨规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 源登记 (
  源键 TEXT PRIMARY KEY,
  轨别 TEXT NOT NULL,
  名称 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 左轨流水 (
  事件编号 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  源键 TEXT NOT NULL,
  事件时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已入左',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 右轨流水 (
  事件编号 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  源键 TEXT NOT NULL,
  事件时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已入右',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 对齐差 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  左金额分 INTEGER,
  右金额分 INTEGER,
  差型 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 悬账单 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  悬边 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 对拍结论 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  左笔数 INTEGER NOT NULL,
  右笔数 INTEGER NOT NULL,
  对齐笔数 INTEGER NOT NULL,
  悬账笔数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  轨别 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 业务键换绑 (
  换绑编号 TEXT PRIMARY KEY,
  轨别 TEXT NOT NULL,
  源键 TEXT NOT NULL DEFAULT '',
  旧键 TEXT NOT NULL,
  新键 TEXT NOT NULL,
  现行读键 TEXT NOT NULL,
  策略 TEXT NOT NULL CHECK (策略 IN ('归档','拒收')),
  拒因 TEXT NOT NULL DEFAULT '',
  valid INTEGER NOT NULL DEFAULT 1,
  时刻 TEXT NOT NULL,
  UNIQUE (轨别, 旧键)
);
"""

# 既有库的列迁移：换绑主链路需要在流水/差/悬账上留读键与原键痕迹。
_列迁移 = [
    ("左轨流水", "读键", "TEXT"),
    ("左轨流水", "换绑编号", "TEXT NOT NULL DEFAULT ''"),
    ("右轨流水", "读键", "TEXT"),
    ("右轨流水", "换绑编号", "TEXT NOT NULL DEFAULT ''"),
    ("对齐差", "读键", "TEXT"),
    ("悬账单", "读键", "TEXT"),
    ("悬账单", "原业务键", "TEXT"),
]


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _现有列(conn, 表: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({表})").fetchall()}


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    for 表, 列, 类型 in _列迁移:
        if 列 not in _现有列(conn, 表):
            conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {类型}")
    # 历史行回填：未涉换绑的行，读键即其业务键、原业务键即其业务键。
    conn.execute("UPDATE 左轨流水 SET 读键=业务键 WHERE 读键 IS NULL OR 读键=''")
    conn.execute("UPDATE 右轨流水 SET 读键=业务键 WHERE 读键 IS NULL OR 读键=''")
    conn.execute("UPDATE 对齐差 SET 读键=业务键 WHERE 读键 IS NULL OR 读键=''")
    conn.execute("UPDATE 悬账单 SET 读键=业务键 WHERE 读键 IS NULL OR 读键=''")
    conn.execute("UPDATE 悬账单 SET 原业务键=业务键 WHERE 原业务键 IS NULL OR 原业务键=''")
    if conn.execute("SELECT COUNT(*) AS c FROM 轨规").fetchone()["c"] == 0:
        for k, v in {
            "站名": "对拍",
            "对齐键": "业务键",
            "金额容差分": "0",
            "骨架": "左轨装载→右轨装载→同键对齐落差→单边悬账→对拍结论",
            "皮": "支付渠道回执 vs 核心记账",
            "去皮": "双源键对齐管道",
        }.items():
            conn.execute("INSERT INTO 轨规(键, 值) VALUES (?, ?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 源登记").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO 源登记(源键,轨别,名称,说明) VALUES (?,?,?,?)",
            ("渠甲", "左", "渠道回执甲", "支付渠道回执流"),
        )
        conn.execute(
            "INSERT INTO 源登记(源键,轨别,名称,说明) VALUES (?,?,?,?)",
            ("核乙", "右", "核心记账乙", "核心系统记账流"),
        )
    conn.commit()
    conn.close()
