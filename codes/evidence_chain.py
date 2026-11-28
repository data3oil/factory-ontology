#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence_chain.py — P0 证据链判定层（2026-11-28）

设计边界（与 model_advisory.py 同纪律）：
- 纯规则、零模型调用、零网络；只从已封口的问答信封推导"哪些证据构成答案链"；
- 默认关闭：未显式设 FACTORY_EVIDENCE_CHAIN=1 时 judge() 返回 None，
  接线点原样跳过，输出与接线前逐字段一致；
- 只出诊断字段：结果挂到 advisory.evidence_chain，绝不碰 hit / answer /
  evidence / coverage，也不新增真相源。

JEV 思路的本地化简版：置信（主候选为确定性来源且覆盖率达标）→ accepted；
不确定（覆盖率不足 / 多来源分歧 / 无证据）→ escalated 并给出升级理由，
但**不擅自改答案**，交给上层或人工复核。

用法（接线在 ask_service.envelope_from_result 的 advisory 挂载点之后）：
    import evidence_chain
    chain = evidence_chain.judge(out)          # 默认关 → None
    out = model_advisory.attach_evidence_chain(out, chain)
"""
import os

ENV_ENABLE = "FACTORY_EVIDENCE_CHAIN"          # "1"/"true"/"yes"/"on" 视为打开

# 与 fusion.py 保持同源词汇（此处只读判定，不改融合权重）
_RULE_SOURCES = {'rule', 'logical', 'axiom'}


def enabled():
    """是否启用证据链判定。未显式配置 → False（默认态完全 no-op）。"""
    return os.environ.get(ENV_ENABLE, "").strip().lower() in ("1", "true", "yes", "on")


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _evidence_refs(evidence, answer):
    """逐条判断证据是否出现在答案文本里，形成可读的证据链条目。"""
    refs = []
    if not isinstance(evidence, list):
        return refs
    blob = answer or ""
    for e in evidence:
        if not isinstance(e, dict):
            continue
        value = e.get("value")
        entity = e.get("entity")
        label = e.get("label")
        iri = e.get("iri")
        hay = " ".join(str(x) for x in (value, entity, label) if x is not None)
        in_answer = bool(hay and any(
            str(x) and str(x) in blob for x in (value, label, entity) if x is not None
        ))
        refs.append({
            "entity": entity,
            "value": value,
            "label": label,
            "iri": iri,
            "source": e.get("source"),
            "rule_id": e.get("rule_id"),
            "in_answer": in_answer,
        })
    return refs


def judge(result):
    """从已封口信封推导证据链判定；默认关闭返回 None。

    入参 result 是 dict（问答信封），只读不写。返回 dict 或 None。
    """
    if not enabled() or not isinstance(result, dict):
        return None
    answer = result.get("answer") or ""
    # 无答案 / 无依据时不做判定：诚实拒答本来就没有"证据链"可言
    if not answer or result.get("no_basis"):
        return None
    evidence = result.get("evidence") or []
    refs = _evidence_refs(evidence, answer)
    if not refs:
        return None

    coverage = _num(result.get("coverage"), 0.0) if result.get("coverage") is not None else None
    mode = result.get("mode")
    deterministic = mode in _RULE_SOURCES
    linked = sum(1 for r in refs if r["in_answer"])

    if deterministic and coverage is not None and coverage >= 0.9 and linked >= 1:
        state, confidence = "accepted", "high"
        reason = "确定性来源且覆盖率达标，证据出现在答案中"
    elif linked >= 1:
        state, confidence = "accepted", "medium"
        reason = "有证据出现在答案中，但来源或覆盖率未达高置信"
    else:
        state, confidence = "escalated", "low"
        reason = "证据未出现在答案文本中，建议人工复核"

    # 字段白名单：判定结果只出诊断，不带 answer/hit（防下游把诊断当结论）。
    return {
        "state": state,
        "confidence": confidence,
        "reason": reason,
        "coverage": coverage,
        "source": mode,
        "linked": linked,
        "total": len(refs),
        "evidence": refs,
    }


if __name__ == "__main__":
    import json
    print(json.dumps({"enabled": enabled(), "env": ENV_ENABLE}, ensure_ascii=False))
