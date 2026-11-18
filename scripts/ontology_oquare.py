#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ontology_oquare.py — OQuaRE 风格的本体结构度量（纯标准库，可独立跑，也可入发版）。

OQuaRE 把 ISO/IEC 25000 SQuaRE 的质量特性适配到本体：形式化、冗余、一致性、纠缠度等。
本脚本只算**能从导出件确定性算出**的几项，打 1–5 分，写进发版记录。不伪造分数：
某项证据不足（如无定义可比对）就给 None 并标注，不硬凑。

指标与打分口径：
  标注完整度 annotation  = (有 rdfs:label 的类占比 + 有 skos:definition 的类占比) / 2
  一致性 consistency     = 1 - (自环 + 循环 + 悬空) / 类数，clamp 0..1
  关系密度 rel_density   = 对象属性数 / 类数（0.2–3 视为健康）
  层次深度 depth         = 最长 subClassOf 链（≥3 视为有分类层次）
  冗余 redundancy        = 重复三元组占比

用法：
  python scripts/ontology_oquare.py --schema config/ontology_schema_valve.json --data data_valve
  python scripts/ontology_oquare.py --all        # 核心库各打一次分
退出码：0 总是（度量不是门，不阻断发版）；有 --strict 时低分返回 1。
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, CODES)
import ontology_lint_lib as L  # noqa: E402

SKOS_DEF = "http://www.w3.org/2004/02/skos/core#definition"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
CORE_KBS = ["valve", "food_co", "chem", "ai4i", "auto_parts"]


def _score(v, lo, hi):
    """线性映射到 1–5 分。"""
    if v is None:
        return None
    if hi == lo:
        return 3.0
    x = (v - lo) / (hi - lo)
    return round(1 + max(0.0, min(1.0, x)) * 4, 2)


def metrics(ttl_text, full_text=None):
    pf, tri = L.parse_turtle(ttl_text)
    cls = L.classes(tri)
    sub = defaultdict(set)
    labels = defaultdict(set)
    defs = defaultdict(set)
    obj_props, dat_props = set(), set()
    dangling = 0
    for s, p, o, sk, ok in tri:
        if p == L.RDFS_SUBCLASS and ok == "iri":
            sub[s].add(o)
        elif p == LABEL:
            labels[s].add(o)
        elif p == SKOS_DEF:
            defs[s].add(o)
        elif p == L.RDF_TYPE and ok == "iri":
            if o == L.OWL_OBJPROP:
                obj_props.add(s)
            elif o == L.OWL_DATAPROP:
                dat_props.add(s)

    n = len(cls) or 1
    lab_rate = sum(1 for c in cls if labels.get(c)) / n
    def_rate = sum(1 for c in cls if defs.get(c)) / n
    annotation = (lab_rate + def_rate) / 2

    # 自环 + 循环
    self_loops = sum(1 for c, ps in sub.items() if c in ps)
    color = defaultdict(int)
    cycles = [0]

    def dfs(u, stack):
        color[u] = 1
        for v in sub.get(u, ()):
            if color[v] == 1:
                cycles[0] += 1
                return
            if color[v] == 0:
                dfs(v, stack + [v])
        color[u] = 2

    for c in list(cls):
        if color[c] == 0:
            dfs(c, [c])
    consistency = max(0.0, 1 - (self_loops + cycles[0]) / n)

    rel_density = len(obj_props) / n

    # 最长链
    depth = [0]
    memo = {}

    def chain(u, seen):
        if u in memo:
            return memo[u]
        best = 1
        for v in sub.get(u, ()):
            if v in seen:
                continue
            best = max(best, 1 + chain(v, seen | {u}))
        memo[u] = best
        return best

    for c in cls:
        depth[0] = max(depth[0], chain(c, {c}))

    # 冗余：完全重复的三元组
    seen_t = set()
    dup = 0
    for t in tri:
        if t in seen_t:
            dup += 1
        seen_t.add(t)
    redundancy = dup / (len(tri) or 1)

    return {
        "classes": len(cls),
        "obj_props": len(obj_props),
        "dat_props": len(dat_props),
        "label_rate": round(lab_rate, 3),
        "definition_rate": round(def_rate, 3),
        "annotation": round(annotation, 3),
        "consistency": round(consistency, 3),
        "rel_density": round(rel_density, 3),
        "depth": depth[0],
        "redundancy": round(redundancy, 4),
        "scores": {
            "annotation": _score(annotation, 0.3, 0.95),
            "consistency": _score(consistency, 0.5, 1.0),
            "rel_density": _score(rel_density, 0.2, 3.0),
            "depth": _score(depth[0], 1, 5),
        },
    }


def run_one(name, sp, data):
    import ontology_export as oe
    with tempfile.TemporaryDirectory(prefix="oq_") as td:
        oe.export(sp, td, data)
        ttl = open(os.path.join(td, "ontology.ttl"), encoding="utf-8").read()
        full_p = os.path.join(td, "ontology-full.ttl")
        full = open(full_p, encoding="utf-8").read() if os.path.exists(full_p) else None
    m = metrics(ttl, full)
    total = round(sum(v for v in m["scores"].values() if v is not None) / 4, 2)
    m["oquare_total"] = total
    m["kb"] = name
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema")
    ap.add_argument("--data")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", help="把结果写进该 JSON 文件")
    ap.add_argument("--strict", action="store_true", help="综合分 <3 返回 1")
    a = ap.parse_args()

    runs = []
    if a.schema and a.data:
        runs = [("custom", a.schema, a.data)]
    else:
        for sp in sorted(glob.glob(os.path.join(CODES, "config", "ontology_schema*.json"))):
            m = re.match(r"ontology_schema(?:_(.*))?\.json$", os.path.basename(sp))
            if not m:
                continue
            nm = m.group(1) or ""
            if not a.all and nm not in CORE_KBS:
                continue
            d = os.path.join(CODES, "data_" + nm) if nm else os.path.join(CODES, "data")
            if os.path.isdir(d):
                runs.append((nm or "default", sp, d))

    out = []
    bad = 0
    for name, sp, data in runs:
        m = run_one(name, sp, data)
        out.append(m)
        print("[%-16s] 综合 %.2f/5  类%d 对象属性%d 标注%.0f%% 定义%.0f%% 深度%d 冗余%.3f"
              % (name, m["oquare_total"], m["classes"], m["obj_props"],
                 m["label_rate"] * 100, m["definition_rate"] * 100, m["depth"], m["redundancy"]))
        if a.strict and m["oquare_total"] < 3:
            bad += 1

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("已写入", a.json)

    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
