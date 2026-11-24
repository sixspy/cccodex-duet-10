"""左轨（渠道回执）装载：换绑契约由共享装载器统一执行。"""
from __future__ import annotations
from app.services.装载 import 装一批 as _装


def 装一批(体: dict) -> dict:
    return _装("左", "左轨流水", 体)
