import os
import sys
import sqlite3
import threading
import time
from pathlib import Path

import pytest

# 每个测试会话使用独立临时库，须在 import app 之前固定数据目录。
_TMP = Path(__file__).resolve().parent / "_tmp_data"
_TMP.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_TMP)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from app.db import 库路径, 连接  # noqa: E402
from app import seed  # noqa: E402


@pytest.fixture()
def client():
    if 库路径.exists():
        库路径.unlink()
    seed.初始化库()
    from app.main import app
    with TestClient(app) as c:
        yield c


def _左事件(编号, 键, 金额=1000, 源="渠甲"):
    return {"事件编号": 编号, "业务键": 键, "金额分": 金额, "源键": 源,
            "事件时": "2026-11-23T10:00:00Z"}


def _右事件(编号, 键, 金额=1000, 源="核乙"):
    return {"事件编号": 编号, "业务键": 键, "金额分": 金额, "源键": 源,
            "事件时": "2026-11-23T10:00:01Z"}


# 1. 无换绑时 happy path 默认口径不变
def test_happy_path_不变(client):
    r = client.post("/api/左轨/夹具/左轨样例")
    assert r.status_code == 200 and r.json()["入流"] == 3
    r = client.post("/api/右轨/夹具/右轨样例")
    assert r.status_code == 200 and r.json()["入流"] == 3
    rep = client.post("/api/对齐/跑").json()
    assert rep["对齐"] == 1 and rep["悬账"] == 2 and rep["归档键总数"] == 0
    差型 = client.get("/api/统计/差型").json()
    assert 差型 == {"对齐": 1, "金额不符": 1, "右缺": 1, "左缺": 1}
    assert client.post("/api/对拍结论/跑").json()["是否通过"] is True
    # 读键默认等于业务键
    for row in client.get("/api/左轨").json():
        assert row["读键"] == row["业务键"] and row["换绑编号"] == ""


# 2. 归档形态：旧键进只读归档，新键现行，读引用不同
def test_归档形态(client):
    client.post("/api/左轨/装载", json={"事件": [
        _左事件("L1", "ORD-100"), _左事件("L2", "ORD-102", 800)]})
    r = client.post("/api/换绑", json={
        "轨别": "左", "源键": "渠甲", "旧键": "ORD-100",
        "新键": "ORD-100N", "策略": "归档"})
    assert r.status_code == 200
    d = r.json()
    # 只读接口字段：旧键、新键、现行读键、归档或拒因、valid
    assert d["旧键"] == "ORD-100" and d["新键"] == "ORD-100N"
    assert d["现行读键"] == "ORD-100"  # 归档形态现行读键指向旧键（归档）
    assert d["策略"] == "归档" and d["valid"] == 1 and d["拒因"] == ""
    assert d["随绑归档笔数"] == 1
    # 详情接口同形
    g = client.get("/api/换绑/左/ORD-100").json()
    assert g["换绑编号"] == d["换绑编号"] and g["valid"] == 1

    # 换绑前旧键流水已被钉成归档命运
    行 = [x for x in client.get("/api/左轨").json() if x["事件编号"] == "L1"][0]
    assert 行["命运"] == "已归档左" and 行["读键"] == "ORD-100"
    assert 行["换绑编号"] == d["换绑编号"]

    # 换绑后只灌旧键 → 进归档，不进现行
    rep = client.post("/api/左轨/装载", json={"事件": [_左事件("L1B", "ORD-100")]}).json()
    assert rep["归档"] == 1 and rep["入流"] == 0
    行b = [x for x in client.get("/api/左轨").json() if x["事件编号"] == "L1B"][0]
    assert 行b["命运"] == "已归档左" and 行b["读键"] == "ORD-100"

    # 灌新键 → 现行可对齐
    client.post("/api/左轨/装载", json={"事件": [_左事件("L1N", "ORD-100N")]})
    client.post("/api/右轨/装载", json={"事件": [_右事件("R1N", "ORD-100N")]})

    rep = client.post("/api/对齐/跑").json()
    assert rep["现行键总数"] == 2  # ORD-100N, ORD-102
    assert rep["归档键总数"] == 1  # ORD-100
    差 = client.get("/api/对齐差").json()
    现行 = {x["业务键"]: x["差型"] for x in 差 if x["差型"] != "归档悬置"}
    assert 现行.get("ORD-100N") == "对齐" and 现行.get("ORD-102") == "右缺"
    assert "ORD-100" not in 现行  # 旧键不进现行对齐集合
    归档行 = [x for x in 差 if x["业务键"] == "ORD-100"]
    assert len(归档行) == 1 and 归档行[0]["差型"] == "归档悬置"
    assert 归档行[0]["读键"] == "ORD-100"
    # 旧键悬账改读归档，仍挂旧键
    悬 = [x for x in client.get("/api/悬账单").json() if x["业务键"] == "ORD-100"]
    assert len(悬) == 1 and 悬[0]["读键"] == "ORD-100" and "归档" in 悬[0]["原因"]
    assert client.post("/api/对拍结论/跑").json()["是否通过"] is True


# 3. 拒收形态：旧键回刷拒收，悬账挂旧键，现行不自动迁键
def test_拒收形态(client):
    client.post("/api/左轨/装载", json={"事件": [_左事件("L1", "ORD-200")]})
    client.post("/api/对齐/跑")
    assert any(x["业务键"] == "ORD-200" for x in client.get("/api/悬账单").json())

    r = client.post("/api/换绑", json={
        "轨别": "左", "源键": "渠甲", "旧键": "ORD-200",
        "新键": "ORD-200N", "策略": "拒收"})
    d = r.json()
    assert d["现行读键"] == "ORD-200N"  # 拒收形态现行读键=新键
    assert d["拒因"] and d["valid"] == 1 and d["随绑归档笔数"] == 0

    # 历史旧键流水不被自动迁键，仍是现行入流
    行 = [x for x in client.get("/api/左轨").json() if x["事件编号"] == "L1"][0]
    assert 行["命运"] == "已入左" and 行["读键"] == "ORD-200"

    # 只灌旧键 → 拒收且悬仍旧，无新流水落库
    rep = client.post("/api/左轨/装载", json={"事件": [_左事件("L1B", "ORD-200")]}).json()
    assert rep["驳回"] == 1 and rep["入流"] == 0 and rep["归档"] == 0
    assert rep["明细"][-1]["命运"] == "驳回-换绑拒收"
    assert all(x["事件编号"] != "L1B" for x in client.get("/api/左轨").json())

    # 灌新键 → 现行
    client.post("/api/左轨/装载", json={"事件": [_左事件("L1N", "ORD-200N")]})
    client.post("/api/对齐/跑")
    悬键 = {x["业务键"] for x in client.get("/api/悬账单").json()}
    assert "ORD-200" in 悬键  # 悬账仍挂旧键
    assert client.post("/api/对拍结论/跑").json()["是否通过"] is True
    # 两形态可区分
    s = client.get("/api/统计/换绑").json()
    assert s["策略"] == {"拒收": 1} and s["归档流水"] == {"左": 0, "右": 0}


# 4. 并发：换绑登记与装载互斥，旧键不得进现行双份
def test_并发互斥(client):
    client.post("/api/左轨/装载", json={"事件": [_左事件("L0", "ORD-300")]})
    db = str(库路径)
    a = sqlite3.connect(db, timeout=0)
    a.execute("BEGIN IMMEDIATE")  # 占写锁
    锁果 = {}

    def 抢锁():
        b = sqlite3.connect(db, timeout=0)
        try:
            b.execute("BEGIN IMMEDIATE")
            锁果["拿到"] = True
        except sqlite3.OperationalError as e:
            锁果["拿到"] = False
            锁果["错"] = str(e)
        finally:
            b.close()

    t = threading.Thread(target=抢锁)
    t.start(); t.join()
    assert 锁果["拿到"] is False and "locked" in 锁果["错"]
    a.commit(); a.close()

    # 高一层不变量：换绑（归档）后，旧键所有流水同命运，现行绝无双份
    client.post("/api/换绑", json={
        "轨别": "左", "源键": "渠甲", "旧键": "ORD-300",
        "新键": "ORD-300N", "策略": "归档"})
    client.post("/api/左轨/装载", json={"事件": [
        _左事件("L1", "ORD-300"), _左事件("L2", "ORD-300")]})
    client.post("/api/左轨/装载", json={"事件": [_左事件("LN", "ORD-300N")]})
    client.post("/api/对齐/跑")
    旧 = [x for x in client.get("/api/左轨").json() if x["业务键"] == "ORD-300"]
    assert 旧 and all(x["命运"] == "已归档左" for x in 旧)  # 钉同成败
    差 = [x for x in client.get("/api/对齐差").json() if x["业务键"] == "ORD-300"]
    assert len(差) == 1 and 差[0]["差型"] == "归档悬置"  # 不双份、不进现行


# 5. 登记校验与只读 404
def test_登记校验(client):
    body = {"轨别": "左", "源键": "渠甲", "旧键": "K", "新键": "K2", "策略": "归档"}
    assert client.post("/api/换绑", json=body).status_code == 200
    assert client.post("/api/换绑", json=body).status_code == 400  # 重复旧键
    bad = dict(body, 旧键="K2", 新键="K2")
    assert client.post("/api/换绑", json=bad).status_code == 400   # 新旧相同
    assert client.post("/api/换绑", json=dict(body, 旧键="K3", 策略="乱")).status_code == 400
    assert client.post("/api/换绑", json=dict(body, 旧键="K4", 源键="无此源")).status_code == 400
    assert client.get("/api/换绑/左/不存在").status_code == 404
    assert len(client.get("/api/换绑").json()) == 1


# 6. 源登记页数据带换绑，概览有水位
def test_源登记与概览(client):
    client.post("/api/换绑", json={
        "轨别": "左", "源键": "渠甲", "旧键": "K", "新键": "K2", "策略": "归档"})
    源 = {x["源键"]: x for x in client.get("/api/源登记").json()}
    assert 源["渠甲"]["换绑数"] == 1
    assert 源["渠甲"]["换绑"][0]["旧键"] == "K"
    o = client.get("/api/概览").json()
    assert "换绑数" in o and "归档左笔数" in o
