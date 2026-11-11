# -*- coding: utf-8 -*-
"""evidence 路由族：证据层的只读端点（+ 一个受保护的重建端点）。

风格与既有 routes/*.py 一致：APIRouter + `import api_server as core` + core.require_key。
默认行为不受影响：本模块只新增路径，不改任何既有路由。
"""
import os

from fastapi import APIRouter, Depends, Query

import api_server as core
import evidence_kb

router = APIRouter()

_DEFAULT_CORPUS = os.environ.get(
    "EVIDENCE_KB_CORPUS", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "docs"))


def _db_path(kb):
    home = os.environ.get("EVIDENCE_KB_HOME",
                          os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "data", "evidence"))
    name = (kb or "default").strip() or "default"
    if not all(ch.isalnum() or ch in "-_" for ch in name):
        name = "default"
    return os.path.join(home, name + ".db")


@router.get("/api/evidence/status", dependencies=[Depends(core.require_key)])
def evidence_status(kb: str = Query("default", description="证据库名")):
    """证据层状态：文档数、段数、日期范围。未建库时 built=false，不报错。"""
    return evidence_kb.status(_db_path(kb))


@router.get("/api/evidence/search", dependencies=[Depends(core.require_key)])
def evidence_search(q: str = Query(..., description="问题"),
                    k: int = Query(8, ge=1, le=30),
                    kb: str = Query("default"),
                    with_verdict: bool = Query(False, description="是否附带四态判定")):
    """检索原文证据：返回 path + 行区间 + 片段，可选附带判定。"""
    db = _db_path(kb)
    if not os.path.exists(db):
        return {"ok": False, "code": "NO_INDEX", "error": "证据库未建立",
                "hint": "python -m evidence_kb.indexer --corpus ../docs --db %s --full" % db}
    rows = evidence_kb.search(db, q, k)
    out = {"ok": True, "query": q, "kb": kb, "count": len(rows), "results": rows}
    if with_verdict:
        out["verdict"] = evidence_kb.judge(q, rows)
    return out


@router.get("/api/evidence/verdict", dependencies=[Depends(core.require_key)])
def evidence_verdict(q: str = Query(...), kb: str = Query("default"),
                     k: int = Query(5, ge=1, le=20)):
    """只给结论：SUPPORTED / WEAK / NOT_FOUND / NOT_JUDGED + 理由 + 证据位置。"""
    db = _db_path(kb)
    if not os.path.exists(db):
        return {"state": "NOT_JUDGED", "why": "证据库未建立", "evidence": []}
    rows = evidence_kb.search(db, q, k)
    v = evidence_kb.judge(q, rows)
    v["query"] = q
    return v


@router.post("/api/evidence/rebuild", dependencies=[Depends(core.require_key)])
def evidence_rebuild(kb: str = Query("default"), corpus: str = Query(""),
                     full: bool = Query(False)):
    """重建证据索引（增量默认）。corpus 留空用默认文档目录。"""
    db = _db_path(kb)
    src = corpus or _DEFAULT_CORPUS
    if not os.path.isdir(src):
        return {"ok": False, "code": "BAD_PATH", "error": "语料目录不存在：%s" % src}
    stat = evidence_kb.build(src, db, full)
    stat["ok"] = True
    stat["corpus"] = src
    return stat
