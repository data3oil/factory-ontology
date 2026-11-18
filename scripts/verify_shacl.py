#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_shacl.py — SHACL 实例校验门（纯标准库，不引 pyshacl/rdflib）。

项目已有 shapes.ttl 导出（ontology_export.build_shapes_ttl），但从未拿它校验过实例。
本门补上这一步：解析 shapes.ttl 的 NodeShape/PropertyShape，对 ontology-full.ttl 的实例
逐条校验，失败即红。只实现本项目实际用到的约束子集（minCount/maxCount/minInclusive/
maxInclusive/datatype），不追求完整 SHACL 语义 —— 与"轻本体、零依赖"红线一致。

为什么要接入：SHACL 是 W3C 推荐标准（2017 Rec，2025 起推 1.2），业界早就是标准件；
项目此前"无 SHACL 校验"是明确的合规缺口（见 docs/20261012 优化清单 P1-4）。

用法：
  python scripts/verify_shacl.py                     # 扫所有可配对 schema
  python scripts/verify_shacl.py --schema X --data Y
退出码：0 = 全过；1 = 有违规。
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

SH = "http://www.w3.org/ns/shacl#"
SH_PATH = SH + "path"
SH_MINCOUNT = SH + "minCount"
SH_MAXCOUNT = SH + "maxCount"
SH_MININC = SH + "minInclusive"
SH_MAXINC = SH + "maxInclusive"
SH_DATATYPE = SH + "datatype"
SH_TARGETCLASS = SH + "targetClass"
SH_PROPERTY = SH + "property"


def _int_lit(tok):
    m = re.match(r'^"?(-?\d+)"?', str(tok).strip())
    return int(m.group(1)) if m else None


def parse_shapes(text):
    """→ (props, nodes)。props: shape→{path,min,max,minInc,maxInc,datatype}；nodes: shape→{target,props}"""
    pf, tri = L.parse_turtle(text)
    props, nodes = defaultdict(dict), defaultdict(lambda: {"target": None, "props": []})
    for s, p, o, sk, ok in tri:
        if p == SH_PATH:
            props[s]["path"] = o
        elif p == SH_MINCOUNT:
            props[s]["min"] = _int_lit(o)
        elif p == SH_MAXCOUNT:
            props[s]["max"] = _int_lit(o)
        elif p == SH_MININC:
            props[s]["minInc"] = _num(o)
        elif p == SH_MAXINC:
            props[s]["maxInc"] = _num(o)
        elif p == SH_DATATYPE:
            props[s]["datatype"] = o
        elif p == SH_TARGETCLASS:
            nodes[s]["target"] = o
        elif p == SH_PROPERTY:
            nodes[s]["props"].append(o)
    return props, nodes


def _num(tok):
    m = re.match(r'^"?(-?\d+(?:\.\d+)?)"?', str(tok).strip())
    return float(m.group(1)) if m else None


def _lit_value(tok):
    """字面量 token → (值字符串, xsd 类型)；非字面量返回 (None, None)。"""
    if not str(tok).startswith('"'):
        return None, None
    s = str(tok)
    end = 1
    while end < len(s):
        if s[end] == "\\":
            end += 2
            continue
        if s[end] == '"':
            end += 1
            break
        end += 1
    val = s[1:end - 1]
    dt = None
    if s.startswith("^^", end):
        raw = s[end + 2:]
        dt = L.local_name(raw.strip("<>"))
    return val, dt


def validate(inst_text, props, nodes):
    """对实例图做 SHACL 子集校验，返回违规列表 [(shape, inst, 说明)]。"""
    pf, tri = L.parse_turtle(inst_text)
    types = defaultdict(set)          # inst → {class}
    values = defaultdict(lambda: defaultdict(list))  # inst → prop → [obj]
    for s, p, o, sk, ok in tri:
        if p == L.RDF_TYPE and ok == "iri":
            types[s].add(o)
        values[s][p].append(o)

    errs = []
    for nshape, meta in nodes.items():
        target = meta.get("target")
        if not target:
            continue
        insts = [i for i, cs in types.items() if target in cs]
        for ishape in meta["props"]:
            spec = props.get(ishape)
            if not spec or not spec.get("path"):
                continue
            path = spec["path"]
            for inst in insts:
                vals = values[inst].get(path, [])
                cnt = len(vals)
                if spec.get("max") is not None and cnt > spec["max"]:
                    errs.append((L.local_name(ishape), L.local_name(inst),
                                 "maxCount=%d 实际=%d" % (spec["max"], cnt)))
                if spec.get("min") is not None and cnt < spec["min"]:
                    errs.append((L.local_name(ishape), L.local_name(inst),
                                 "minCount=%d 实际=%d" % (spec["min"], cnt)))
                for v in vals:
                    num = _num(v) if not str(v).startswith('"') else _num(v)
                    if spec.get("minInc") is not None and num is not None and num < spec["minInc"]:
                        errs.append((L.local_name(ishape), L.local_name(inst),
                                     "minInclusive=%s 实际=%s" % (spec["minInc"], num)))
                    if spec.get("maxInc") is not None and num is not None and num > spec["maxInc"]:
                        errs.append((L.local_name(ishape), L.local_name(inst),
                                     "maxInclusive=%s 实际=%s" % (spec["maxInc"], num)))
                    if spec.get("datatype"):
                        _val, dt = _lit_value(v)
                        want = L.local_name(spec["datatype"])
                        if dt and want and dt != want:
                            errs.append((L.local_name(ishape), L.local_name(inst),
                                         "datatype 期望 %s 实际 %s" % (want, dt)))
    return errs


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema")
    ap.add_argument("--data")
    ap.add_argument("--all", action="store_true", help="全量库（默认只跑核心 5 库）")
    a = ap.parse_args()
    import ontology_export as oe

    runs = [("custom", a.schema, a.data)] if (a.schema and a.data) else _pairs(a.all)
    if not runs:
        print("自检结果: 没有可配对的数据目录，跳过（0 库）")
        return 0

    bad = 0
    for name, sp, data in runs:
        with tempfile.TemporaryDirectory(prefix="shacl_") as td:
            oe.export(sp, td, data)
            shapes_p = os.path.join(td, "shapes.ttl")
            full_p = os.path.join(td, "ontology-full.ttl")
            if not (os.path.exists(shapes_p) and os.path.exists(full_p)):
                continue
            props, nodes = parse_shapes(open(shapes_p, encoding="utf-8").read())
            errs = validate(open(full_p, encoding="utf-8").read(), props, nodes)
        if errs:
            bad += 1
            print("[FAIL] %-16s %d 条违规" % (name, len(errs)))
            for sh, inst, msg in errs[:8]:
                print("        %-34s %-22s %s" % (sh, inst, msg))
            if len(errs) > 8:
                print("        ... 另有 %d 条" % (len(errs) - 8))
        else:
            print("[PASS] %-16s %d 形状全过" % (name, len(props)))

    print("\n自检结果: %s（%d 库，%d 库有违规）"
          % ("通过" if bad == 0 else "不通过", len(runs), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
