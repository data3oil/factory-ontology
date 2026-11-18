#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_oops.py — 本体建模坑静态 lint（对标 OOPS! catalogue，纯标准库）。

OOPS!（oops.linkeddata.es，P01–P41）是领域里最成熟的本体坑清单。这里只取**适用于轻量本体、
且能确定性自动判定**的几条，不要求上形式化推理，与项目"轻量够用"的红线不冲突：

  P04 孤立元素：类既无父类、无子类，也没有任何属性挂载（根类豁免）
  P06 循环继承：subClassOf 成环（含自引用）
  P10 缺标注：类缺 rdfs:label（中文可读名）
  P11 缺域值域：owl:ObjectProperty 缺 rdfs:domain 或 rdfs:range
  P19 多域声明：同一属性被声明多个 rdfs:domain（易造成推理歧义）

用法：
  python scripts/verify_oops.py                          # 扫所有可配对 schema
  python scripts/verify_oops.py --schema X --data Y
退出码：0 = 无高危问题；1 = 有。
"""
import argparse
import glob
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

ROOT_CLASSES = {"Enterprise", "BusinessObject"}


CORE_KBS = ["valve", "food_co", "chem", "ai4i", "auto_parts"]


def _pairs(all_kbs=False):
    out = []
    for sp in sorted(glob.glob(os.path.join(CODES, "config", "ontology_schema*.json"))):
        base = os.path.basename(sp)
        m = re.match(r"ontology_schema(?:_(.*))?\.json$", base)
        if not m:
            continue
        name = m.group(1) or ""
        if not all_kbs and name not in CORE_KBS:
            continue
        cand = [os.path.join(CODES, "data_" + name)] if name else []
        cand.append(os.path.join(CODES, "data"))
        data = next((d for d in cand if os.path.isdir(d)), None)
        if data:
            out.append((name or "default", sp, data))
    return out


def lint(text, ns):
    """对 TBox 文本做 P04/P06/P10/P11/P19 判定。"""
    pf, tri = L.parse_turtle(text)
    cls = L.classes(tri)
    sub_of, supers = defaultdict(set), defaultdict(set)
    domains, ranges, labels = defaultdict(set), defaultdict(set), defaultdict(set)
    obj_props = set()
    for s, p, o, sk, ok in tri:
        if p == L.RDFS_SUBCLASS and ok == "iri":
            sub_of[s].add(o)
            supers[o].add(s)
        elif p == L.RDFS_DOMAIN and ok == "iri":
            domains[s].add(o)
        elif p == L.RDFS_RANGE and ok == "iri":
            ranges[s].add(o)
        elif p.startswith("http://www.w3.org/2000/01/rdf-schema#label"):
            labels[s].add(o)
        elif p == L.RDF_TYPE and ok == "iri" and o == L.OWL_OBJPROP:
            obj_props.add(s)
    # rdfs:label 的谓词是完整 IRI，上面 p 已是展开值；用常量更稳
    LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
    labels = defaultdict(set, {s: set() for s in cls})
    for s, p, o, sk, ok in tri:
        if p == LABEL:
            labels[s].add(o)

    issues = []
    used_as_endpoint = set()
    for v in list(domains.values()) + list(ranges.values()):
        used_as_endpoint |= v

    for c in sorted(cls):
        if not c.startswith(ns):
            continue
        name = L.local_name(c)
        # P04 孤立（根类豁免）
        if name not in ROOT_CLASSES and not sub_of.get(c) and not supers.get(c) and c not in used_as_endpoint:
            issues.append(("P04 孤立元素", name))
        # P10 缺标注
        if not labels.get(c):
            issues.append(("P10 缺 rdfs:label", name))
        # P06/P22 自引用
        if c in sub_of.get(c, set()):
            issues.append(("P06 自引用继承", name))

    # P06 循环继承（DFS）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(int)
    cycle = []

    def dfs(u, stack):
        color[u] = GRAY
        for v in sub_of.get(u, ()):
            if not v.startswith(ns):
                continue
            if color[v] == GRAY:
                cycle.append(" -> ".join(L.local_name(x) for x in stack + [v]))
                return True
            if color[v] == WHITE and dfs(v, stack + [v]):
                return True
        color[u] = BLACK
        return False

    for c in sorted(cls):
        if c.startswith(ns) and color[c] == WHITE:
            if dfs(c, [c]):
                break
    if cycle:
        issues.append(("P06 循环继承", cycle[0]))

    for p in sorted(obj_props):
        name = L.local_name(p)
        if not domains.get(p):
            issues.append(("P11 对象属性缺 domain", name))
        if not ranges.get(p):
            issues.append(("P11 对象属性缺 range", name))
        if len(domains.get(p, ())) > 1:
            issues.append(("P19 多域声明", "%s: %d 个" % (name, len(domains[p]))))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema")
    ap.add_argument("--data")
    ap.add_argument("--all", action="store_true", help="全量库（默认只跑核心 5 库）")
    a = ap.parse_args()
    import ontology_export as oe
    import schema_ontology as so

    runs = [("custom", a.schema, a.data)] if (a.schema and a.data) else _pairs(a.all)
    if not runs:
        print("自检结果: 没有可配对的数据目录，跳过（0 库）")
        return 0

    bad = 0
    for name, sp, data in runs:
        with tempfile.TemporaryDirectory(prefix="oops_") as td:
            oe.export(sp, td, data)
            p = os.path.join(td, "ontology.ttl")
            text = open(p, encoding="utf-8").read()
            schema = so.load_schema(sp)
            ns = (schema.get("_ns") or so.resolve_namespace(schema))["ns"]
            issues = lint(text, ns)
        if issues:
            bad += 1
            print("[WARN] %-16s %d 条" % (name, len(issues)))
            for rule, detail in issues[:10]:
                print("        %-22s %s" % (rule, detail))
            if len(issues) > 10:
                print("        ... 另有 %d 条" % (len(issues) - 10))
        else:
            print("[PASS] %-16s 无坑" % name)

    print("\n自检结果: %s（%d 库，%d 库有告警）"
          % ("通过" if bad == 0 else "有告警", len(runs), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
