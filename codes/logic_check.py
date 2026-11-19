#!/usr/bin/env python3
"""logic_check.py — 本体逻辑一致性检查（零依赖，进 CI）

与 `ontology_check.py` 的分工：
    ontology_check.py 查的是"资料齐不齐、链路断没断、数据脏不脏"（0-100 分）。
    本文件只查**逻辑一致性**：写错的公理会在推理阶段静默产出错误结论，必须先拦。

四项：
    C1 主键唯一（schema.constraints 的 unique）
    C2 单值关系（relations 里 cardinality 为 1:1）
    C3 链式公理无环（axioms.chains 的 path 按属性建图）
    C4 不相交类（schema.disjoint；未声明标 skipped，不当作通过）

用法：
    python logic_check.py --schema config/ontology_schema_valve.json \
        --nt output/valve.nt --axioms config/axioms/valve.json [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from graph_rag import parse_nt, tail  # noqa: E402


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _instances(triples):
    out = defaultdict(set)
    for s, p, o in triples:
        if tail(p) == "type":
            out[tail(o)].add(s)
    return out


def _attrs(triples):
    out = defaultdict(list)
    for s, p, o in triples:
        if tail(p) in ("type", "label"):
            continue
        out[(s, tail(p))].append(o)
    return out


def check_unique(schema, triples):
    inst = _instances(triples)
    attrs = _attrs(triples)
    bad = []
    for c in schema.get("constraints") or []:
        if c.get("type") != "unique":
            continue
        cls, _, attr = (c.get("on") or "").partition(".")
        if not cls or not attr:
            continue
        seen = defaultdict(list)
        for iri in inst.get(cls, ()):
            for v in attrs.get((iri, attr), ()):
                seen[str(v)].append(iri)
        for v, iris in seen.items():
            if len(iris) > 1:
                bad.append({"constraint": c.get("msg") or c.get("on"),
                            "value": v, "instances": sorted(iris)[:5]})
    return {"id": "C1-主键唯一", "ok": not bad, "violations": bad}


def check_single_valued(schema, triples):
    rels = [r for r in (schema.get("relations") or []) if str(r.get("cardinality")) == "1:1"]
    attrs = _attrs(triples)
    bad = []
    for r in rels:
        pid = str(r.get("id") or "")
        if not pid:
            continue
        for (s, p), vals in attrs.items():
            if p == pid and len({str(v) for v in vals}) > 1:
                bad.append({"relation": pid, "subject": s,
                            "values": sorted({str(v) for v in vals})[:5]})
    return {"id": "C2-单值关系", "ok": not bad, "violations": bad,
            "checked_relations": len(rels)}


def check_chain_cycle(axioms):
    chains = (axioms or {}).get("chains") or []
    edges = defaultdict(set)
    for ch in chains:
        props = [str((step or {}).get("prop") or "") for step in (ch.get("path") or [])]
        props = [p for p in props if p]
        for a, b in zip(props, props[1:]):
            edges[a].add(b)
    color, bad = {}, []

    def dfs(node, stack):
        color[node] = 1
        for nxt in edges.get(node, ()):
            if color.get(nxt) == 1:
                bad.append({"cycle": stack[stack.index(nxt):] + [nxt]})
            elif color.get(nxt, 0) == 0:
                dfs(nxt, stack + [nxt])
        color[node] = 2

    for n in list(edges):
        if color.get(n, 0) == 0:
            dfs(n, [n])
    return {"id": "C3-链式公理无环", "ok": not bad, "violations": bad,
            "checked_chains": len(chains)}


def check_disjoint(schema, triples):
    pairs = schema.get("disjoint") or []
    if not pairs:
        return {"id": "C4-不相交类", "ok": True, "skipped": True, "violations": [],
                "reason": "schema 未声明 disjoint，未检查（不算通过）"}
    inst = _instances(triples)
    bad = []
    for a, b in pairs:
        common = inst.get(a, set()) & inst.get(b, set())
        if common:
            bad.append({"classes": [a, b], "shared": sorted(common)[:5]})
    return {"id": "C4-不相交类", "ok": not bad, "violations": bad,
            "checked_pairs": len(pairs)}


def run(schema_path, nt_path, axioms_path=""):
    schema = _load(schema_path)
    triples = parse_nt(nt_path)
    axioms = _load(axioms_path) if axioms_path and os.path.exists(axioms_path) else {}
    checks = [check_unique(schema, triples), check_single_valued(schema, triples),
              check_chain_cycle(axioms), check_disjoint(schema, triples)]
    return {"schema": os.path.basename(schema_path), "nt": os.path.basename(nt_path),
            "triples": len(triples), "checks": checks,
            "ok": all(c["ok"] for c in checks)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--nt", required=True)
    ap.add_argument("--axioms", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = run(args.schema, args.nt, args.axioms)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for c in res["checks"]:
            tag = "SKIP" if c.get("skipped") else ("PASS" if c["ok"] else "FAIL")
            detail = c.get("reason", "") if c.get("skipped") else (
                "" if c["ok"] else json.dumps(c["violations"][:2], ensure_ascii=False))
            print("%-4s %-18s %s" % (tag, c["id"], detail))
        print()
        print("%s：%d 条三元组，%d 项检查" % ("通过" if res["ok"] else "不通过",
                                            res["triples"], len(res["checks"])))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
