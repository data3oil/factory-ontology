# -*- coding: utf-8 -*-
"""证据层单测：建库、检索、判定四态、状态。不依赖服务、不依赖外部模型。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evidence_kb                                           # noqa: E402

DOC = """# 1# 循环水泵 设备档案

## 基本信息
- 设备名称：1# 循环水泵
- 安装位置：循环水站

## 维护要求
- 润滑周期：运行 500 小时加注一次润滑脂。
- 大修周期：累计运行 8000 小时或 24 个月。

# 质量口径

## 一次交检合格率
- 定义：首次检验合格数量 ÷ 交检数量 × 100%。
"""


def _mk():
    d = tempfile.mkdtemp(prefix="evkb_")
    os.makedirs(os.path.join(d, "corpus"))
    with open(os.path.join(d, "corpus", "a.md"), "w", encoding="utf-8") as f:
        f.write(DOC)
    return d


def test_build_and_status():
    d = _mk()
    db = os.path.join(d, "kb.db")
    stat = evidence_kb.build(os.path.join(d, "corpus"), db, full=True)
    assert stat["docs"] == 1 and stat["segs"] >= 2, stat
    st = evidence_kb.status(db)
    assert st["built"] and st["docs"] == 1 and st["segs"] == stat["segs"]


def test_segment_limits():
    segs = list(evidence_kb.segment(DOC))
    assert segs, "切段为空"
    for lf, lt, head, body in segs:
        assert len(body) <= 1200, (len(body), head)
        assert lf >= 1 and lt >= lf


def test_search_returns_line_range():
    d = _mk()
    db = os.path.join(d, "kb.db")
    evidence_kb.build(os.path.join(d, "corpus"), db, full=True)
    rows = evidence_kb.search(db, "润滑周期多久", 3)
    assert rows, "检索为空"
    top = rows[0]
    assert top["path"] == "a.md" and top["line_from"] >= 1 and top["line_to"] >= top["line_from"]
    assert "500 小时" in top["snippet"] or "润滑" in top["snippet"]


def test_verdict_states():
    d = _mk()
    db = os.path.join(d, "kb.db")
    evidence_kb.build(os.path.join(d, "corpus"), db, full=True)
    ok = evidence_kb.judge("润滑周期多久", evidence_kb.search(db, "润滑周期多久", 5))
    assert ok["state"] in ("SUPPORTED", "WEAK"), ok
    miss = evidence_kb.judge("AGV 小车避障算法", evidence_kb.search(db, "AGV 小车避障算法", 5))
    assert miss["state"] in ("NOT_FOUND", "NOT_JUDGED"), miss


def test_judge_never_raises_on_bad_input():
    v = evidence_kb.judge("", [])
    assert v["state"] in ("NOT_FOUND", "NOT_JUDGED")
    v2 = evidence_kb.judge("任何问题", [{"path": None, "line_from": None}])
    assert v2["state"] in ("SUPPORTED", "WEAK", "NOT_FOUND", "NOT_JUDGED")


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
