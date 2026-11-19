#!/usr/bin/env python3
"""export_aas.py — 把本体实例导出成 AAS（Asset Administration Shell）子模型 JSON

用途：
    工业 4.0 客户（德系、汽车供应链）常要求数据以 AAS 形态交换。
    本脚本把每个设备实例导出成一个 AAS Submodel，属性放进 SubmodelElementCollection，
    并对齐 IOF 映射（semanticId 指向 IOF Core 概念）。

口径：
    - 只做"导出"，不实现 AAS 服务器、不做 AAS API 交互。
    - semanticId 用 IOF construct 命名空间；没有映射的类留空并标注。
    - 输出是 AAS 3.0 风格的子模型片段，字段名按规范 camelCase。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "codes"))

from graph_rag import parse_nt, tail  # noqa: E402

NS = "http://factory.example/ontology#"
IOF = "https://spec.industrialontologies.org/ontology/construct/"


def build(nt_path, mapping_path, class_filter="Equipment"):
    triples = parse_nt(nt_path)
    mapping = json.load(open(mapping_path, encoding="utf-8")) if mapping_path else {"mappings": []}
    by_local = {m["local"]: m["iof"] for m in mapping.get("mappings") or []}

    insts, attrs, types = {}, {}, {}
    for s, p, o in triples:
        pn = tail(p)
        if pn == "type":
            types.setdefault(s, []).append(tail(o))
        elif pn != "label":
            attrs.setdefault(s, {})[pn] = o

    out = []
    for inst, ts in types.items():
        cls = ts[0] if ts else ""
        if class_filter and class_filter.lower() not in cls.lower():
            continue
        iof_irif = IOF + by_local.get(cls, "")
        elements = []
        for k, v in (attrs.get(inst) or {}).items():
            if isinstance(v, str) and v.startswith("http"):
                continue
            elements.append({
                "modelType": "Property",
                "idShort": k,
                "valueType": "xs:string" if not str(v).replace(".", "").isdigit() else "xs:double",
                "value": str(v),
            })
        out.append({
            "modelType": "Submodel",
            "idShort": "%s_%s" % (cls, tail(inst)),
            "id": "urn:aas:%s:%s" % (cls, tail(inst)),
            "semanticId": {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": iof_irif}]} if by_local.get(cls) else None,
            "submodelElements": [{
                "modelType": "SubmodelElementCollection",
                "idShort": "Properties",
                "value": elements,
            }],
        })
    return {"assetAdministrationShells": [], "submodels": out,
            "meta": {"source": os.path.basename(nt_path), "count": len(out),
                     "iofMapping": bool(by_local)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nt", required=True)
    ap.add_argument("--mapping", default=os.path.join(ROOT, "codes", "config", "iof_mapping.json"))
    ap.add_argument("--class-filter", default="Equipment")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = build(args.nt, args.mapping, args.class_filter)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("导出 %d 个 AAS 子模型 -> %s" % (doc["meta"]["count"], args.out))
    return 0 if doc["meta"]["count"] else 1


if __name__ == "__main__":
    sys.exit(main())
