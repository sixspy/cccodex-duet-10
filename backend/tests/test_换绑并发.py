"""线程级并发对打：换绑登记 vs 旧键装载，验证写锁串行、无双成功。"""
import os
import sys
import threading
from pathlib import Path

import pytest

_TMP = Path(__file__).resolve().parent / "_tmp_data_conc"
_TMP.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_TMP)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from app.db import 库路径  # noqa: E402
from app import seed  # noqa: E402


def _左事件(编号, 键):
    return {"事件编号": 编号, "业务键": 键, "金额分": 1000,
            "源键": "渠甲", "事件时": "2026-11-23T10:00:00Z"}


def test_换绑与装载并发无双成功():
    if 库路径.exists():
        库路径.unlink()
    seed.初始化库()
    from app.main import app

    with TestClient(app) as c:
        c.post("/api/左轨/装载", json={"事件": [_左事件("L0", "ORD-900")]})
        果 = {}

        def 换绑():
            果["换绑"] = c.post("/api/换绑", json={
                "轨别": "左", "源键": "渠甲", "旧键": "ORD-900",
                "新键": "ORD-900N", "策略": "归档"}).status_code

        def 装载():
            果["装载"] = c.post(
                "/api/左轨/装载", json={"事件": [_左事件("LRACE", "ORD-900")]}
            ).json()

        t1, t2 = threading.Thread(target=换绑), threading.Thread(target=装载)
        t1.start(); t2.start(); t1.join(); t2.join()

        assert 果["换绑"] == 200
        # 装载要么换绑前（入流，随后被换绑 UPDATE 统一钉归档），要么换绑后（直接归档），
        # 不允许出现「换绑成功 + 旧键又现行入流」的双成功中间态。
        rep = 果["装载"]
        assert rep["入流"] + rep["归档"] == 1 and rep["驳回"] == 0
        rows = c.get("/api/左轨").json()
        旧 = [x for x in rows if x["业务键"] == "ORD-900"]
        assert len(旧) == 2
        assert all(x["命运"] == "已归档左" and x["读键"] == "ORD-900" for x in 旧)
        c.post("/api/对齐/跑")
        差 = [x for x in c.get("/api/对齐差").json() if x["业务键"] == "ORD-900"]
        assert len(差) == 1 and 差[0]["差型"] == "归档悬置"
