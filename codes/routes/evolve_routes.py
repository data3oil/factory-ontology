# -*- coding: utf-8 -*-
"""evolve 路由族 — 本体自演进（人在环）的 6 个端点。

与 routes/ 其余族同构：函数体逐字搬运；对 api_server 模块级名字改写成 ``core.X``，
fastapi/pydantic 导入名在本模块显式重新导入；路由路径/鉴权依赖/返回结构不变。
非路由助手（_pick_text_values / _auto_evolve_after_sync）留在 api_server.py：
admin_sync 也在调它们。
回归门：scripts/verify_ontology_evolve.py + codes/tests 单测 + codes/e2e_test.py。
"""

from pydantic import BaseModel

from fastapi import Depends

from fastapi import APIRouter

import api_server as core

router = APIRouter()


class EvolveConfirmReq(BaseModel):
    name: str
    node: str | None = None
    aliases: list | None = None
    cls: str | None = None


class EvolveRejectReq(BaseModel):
    name: str


class EvolveRollbackReq(BaseModel):
    target_version: int | None = None


@router.get("/api/ontology/evolve/pending", dependencies=[Depends(core.require_key)])
def evolve_pending():
    """本体自演进：列出待确认候选（只读，不改词典）。"""
    from ontology_evolve import pending as _ev_pending, alias_version as _ev_alias_version
    kb = core._active_kb()
    return {"ok": True, "kb": kb, "version": _ev_alias_version(kb), "pending": _ev_pending(kb)}


@router.post("/api/ontology/evolve/confirm", dependencies=[Depends(core.require_key)])
def evolve_confirm(req: EvolveConfirmReq):
    """本体自演进：人拍板 —— 候选并入词典 synonym_map + 在本体里长出一个节点（版本+1 + 审计 + 快照）。"""
    from ontology_evolve import (confirm as _ev_confirm, alias_version as _ev_alias_version,
                                add_evolve_node as _ev_add_node)
    kb = core._active_kb()
    ok, msg = _ev_confirm(req.name, req.node, kb=kb, aliases=req.aliases)
    node_uri = ""
    if ok:
        # 本体自演进：除词典别名外，再往「演化节点」里长一个真节点（图谱里可见）。
        try:
            _n_ok, _n_msg = _ev_add_node(req.name, req.cls or "Evolved", kb, aliases=req.aliases)
            if _n_ok:
                node_uri = "%s_%s" % (req.cls or "Evolved", req.name)
            else:
                core.logger.info("evolve 节点未新增(可能已存在): %s" % _n_msg)
        except Exception as _e:
            core.logger.warning("evolve 节点写入失败(词典已并入,不影响): %s" % _e)
    core._invalidate_kb(kb)
    return {"ok": ok, "message": msg, "version": _ev_alias_version(kb), "node": node_uri}


@router.post("/api/ontology/evolve/reject", dependencies=[Depends(core.require_key)])
def evolve_reject(req: EvolveRejectReq):
    """本体自演进：拒绝候选（记住不再提，不进词典）。"""
    from ontology_evolve import reject as _ev_reject
    _kb2 = core._active_kb()
    ok, msg = _ev_reject(req.name, kb=_kb2)
    core._invalidate_kb(_kb2)
    return {"ok": ok, "message": msg}


@router.post("/api/ontology/evolve/rollback", dependencies=[Depends(core.require_key)])
def evolve_rollback(req: EvolveRollbackReq):
    """本体自演进：回退词典到某版本（默认上一版）。"""
    from ontology_evolve import rollback as _ev_rollback, alias_version as _ev_alias_version
    kb = core._active_kb()
    ok, msg = _ev_rollback(req.target_version, kb=kb)
    core._invalidate_kb(kb)
    return {"ok": ok, "message": msg, "version": _ev_alias_version(kb)}


@router.get("/api/ontology/evolve/history", dependencies=[Depends(core.require_key)])
def evolve_history():
    """本体自演进：版本/快照/待确认/已拒绝 概览。"""
    from ontology_evolve import history as _ev_history
    return {"ok": True, **_ev_history(kb=core._active_kb())}


@router.post("/api/ontology/evolve/trigger", dependencies=[Depends(core.require_key)])
def evolve_trigger():
    """手动触发一次本体自演化（重读数据目录 → 提候选 → 待确认区）。"""
    return core._auto_evolve_after_sync()
