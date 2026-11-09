# -*- coding: utf-8 -*-
"""axiom 路由族 — 本体公理集的读、写、生成、试跑（4 个端点）。

上次推理结果由 GET /api/axioms 一起返回（summary.last_run），不另开端点：
前端没有第二个入口的路由就是死链，工程门会拦。

与其他路由族同构：只做协议层（校验入参、调 axiom_service、包返回），
公理逻辑全在 codes/axiom_service.py，推理全在 codes/axiom_reason.py。

设计边界：
- 写操作只动 config/axioms/<kb>.json，动之前自动备份带时间戳的副本。
- 跑推理只写 output/reason/<kb>/（已 gitignore），不碰 nt / 词典 / 规则引擎。
- 所有端点走 require_key；kb 越权由 core._kb_guard 拦。
"""

from pydantic import BaseModel

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

import api_server as core


router = APIRouter()


class AxiomSaveReq(BaseModel):
    kb: str = ""
    axioms: dict


class AxiomKbReq(BaseModel):
    kb: str = ""


def _kb(name):
    kb = (name or "").strip() or core._active_kb()
    core._kb_guard(kb)
    return kb


@router.get("/api/axioms", dependencies=[Depends(core.require_key)])
def axioms_get(kb: str = ""):
    """取该库公理集（文件不存在则按跨行业种子生成一份，并标记 generated）。"""
    import os

    import axiom_service as axs

    k = _kb(kb)
    existed = os.path.exists(axs.axiom_path(k))
    doc = axs.load(k)
    return {"ok": True, "kb": k, "generated": not existed, "path": axs.axiom_path(k),
            "axioms": doc, "summary": axs.summary(k)}


@router.put("/api/axioms", dependencies=[Depends(core.require_key)])
def axioms_put(req: AxiomSaveReq):
    """保存公理集。结构不合规直接拒绝，不写半成品文件。"""
    import axiom_service as axs

    k = _kb(req.kb)
    ok, err = axs.save(k, req.axioms)
    if not ok:
        # 结构不合规是客户端错误，用 4xx——200+ok:false 会让"保存失败"在网关/日志里
        # 看起来像成功，BFF 与前端也只能靠 body 猜。
        return JSONResponse(status_code=400, content={"ok": False, "kb": k, "error": err})
    return {"ok": True, "kb": k, "summary": axs.summary(k)}


@router.post("/api/axioms/seed", dependencies=[Depends(core.require_key)])
def axioms_seed(req: AxiomKbReq):
    """按种子重新生成（覆盖前先备份）。覆盖的是公理文件，不动推理产物。"""
    import axiom_service as axs

    k = _kb(req.kb)
    doc = axs.generate(k)
    ok, err = axs.save(k, doc)
    if not ok:
        return {"ok": False, "kb": k, "error": err}
    return {"ok": True, "kb": k, "axioms": axs.load(k), "summary": axs.summary(k)}


@router.post("/api/axioms/reason", dependencies=[Depends(core.require_key)])
def axioms_reason(req: AxiomKbReq):
    """跑一次推理，返回统计与样例。失败不静默，error 里说明原因。"""
    import axiom_service as axs

    k = _kb(req.kb)
    res = axs.run(k)
    if not res.get("ok"):
        return {"ok": False, "kb": k, "error": res.get("error")}
    return {"ok": True, "kb": k, "report": res["report"], "summary": axs.summary(k)}
