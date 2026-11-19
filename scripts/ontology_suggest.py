#!/usr/bin/env python3
"""ontology_suggest.py — 本体扩展候选建议（离线、只出候选，不写库）

定位：
    和 `ontology_evolve.py` 的人在环确认互补，不重复。本脚本只做"从 CSV 看出一批
    值得补的类/属性/枚举/关系"，输出 pending JSON，由人确认后走既有 evolve 流程。

四条规则（都是可解释的确定性规则，不调模型）：
    R1 表名 → 类建议（表名不在 schema.entities 里）
    R2 文本列的取值数 <= 30 且非高基数 → 枚举类候选（如 状态/等级）
    R3 列名以 _id 结尾且能在别的表找到同名列 → 关系候选
    R4 列名与 schema 属性差异（如 device_type vs deviceType）→ 别名候选

用法：
    python scripts/ontology_suggest.py --data data_valve --schema codes/config/ontology_schema_valve.json \
        --out codes/config/evolve_pending_suggest.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_csvs(data_dir):
    tables = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if rows:
            tables[name] = rows
    return tables


def _is_measure(vals):
    try:
        for v in vals:
            float(v)
        return True
    except ValueError:
        return False


def _is_date(vals):
    return all(re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", v) for v in list(vals)[:5])


def camel(s: str) -> str:
    parts = re.split(r"[_\-]", s)
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def suggest(tables, schema):
    ents = {e.get("table") for e in (schema.get("entities") or []) if e.get("table")}
    ent_names = {e.get("id") for e in (schema.get("entities") or [])}
    rel_fks = {str((r.get("fk") or "")).split(".")[-1] for r in (schema.get("relations") or [])}
    out = {"classes": [], "enums": [], "relations": [], "aliases": []}

    all_ids = {}
    for name, rows in tables.items():
        cols = list(rows[0].keys())
        for c in cols:
            if c.endswith("_id") or c == "id":
                all_ids.setdefault(c, []).append(name)

    for name, rows in tables.items():
        cls = "".join(p[:1].upper() + p[1:] for p in name.split("_"))
        if name not in ents and cls not in ent_names:
            out["classes"].append({"table": name, "suggest": cls,
                                   "reason": "R1 表名不在 schema.entities 中"})
        cols = list(rows[0].keys())
        for c in cols:
            vals = {str(r.get(c) or "").strip() for r in rows}
            vals.discard("")
            name_like = re.search(r"name|parts|code|model|_no$|内容|名称", c, re.I)
            if (1 < len(vals) <= 30 and not c.endswith("_id") and c != "id"
                    and not name_like
                    and not _is_measure(vals) and not _is_date(vals)):
                out["enums"].append({"table": name, "column": c, "values": sorted(vals)[:30],
                                     "reason": "R2 低基数文本列，可建枚举类"})
            if c.endswith("_id") and c != "id" and len(all_ids.get(c, [])) >= 2:
                out["relations"].append({"table": name, "column": c,
                                         "targets": [t for t in all_ids[c] if t != name],
                                         "reason": "R3 同名外键出现在多张表"})
            if c not in rel_fks and camel(c) and c != camel(c):
                out["aliases"].append({"table": name, "column": c, "camel": camel(c),
                                       "reason": "R4 蛇形列名与实例驼峰键不一致"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    tables = load_csvs(args.data)
    schema = json.load(open(args.schema, encoding="utf-8"))
    res = suggest(tables, schema)
    res["_meta"] = {"tables": len(tables), "note": "只出候选，确认后走 ontology_evolve，不直接写词典"}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("类 %d / 枚举 %d / 关系 %d / 别名 %d -> %s"
          % (len(res["classes"]), len(res["enums"]), len(res["relations"]),
             len(res["aliases"]), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
