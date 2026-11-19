#!/usr/bin/env python3
"""verify_iof_mapping.py — IOF 映射自检（离线、进 CI）

检查什么：
    1. IRI 前缀正确，类名存在于 IOF Core 类清单快照；
    2. 本地类名存在于 schema；
    3. 一个本地类只映射一次；
    4. 覆盖率 = 已映射本地类 / schema 实体类，低于 60% 报红（避免只做两三个装样子）。

不联网。类清单来自 `config/iof_core_classes.txt`（core/Core.rdf 快照，MIT）。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "codes", "config")


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    mapping = load_json(os.path.join(CONFIG, "iof_mapping.json"))
    schema = load_json(os.path.join(CONFIG, "ontology_schema_valve.json"))
    classes = {ln.strip() for ln in open(os.path.join(CONFIG, "iof_core_classes.txt"),
                                        encoding="utf-8") if ln.strip() and not ln.startswith("#")}
    # 只对"有物理表的实体"算覆盖率：schema 里的 Valve_batches__已入库 这类
    # 是按枚举值自动生成的分类类，不是需要对齐 IOF 的业务实体。
    business = {e["id"] for e in (schema.get("entities") or []) if e.get("table")}
    local = business or {e["id"] for e in (schema.get("entities") or [])}
    prefix = mapping.get("iri_prefix") or ""
    fails, seen = [], set()

    for m in mapping.get("mappings") or []:
        lc, ic = m.get("local"), m.get("iof")
        if lc not in local:
            fails.append("本地类不存在: %s" % lc)
        if ic not in classes:
            fails.append("IOF 类不存在: %s" % ic)
        if lc in seen:
            fails.append("重复映射: %s" % lc)
        seen.add(lc)
        if not m.get("relation", "").startswith("skos:"):
            fails.append("关系应为 skos:* 映射谓词: %s" % lc)
        if not prefix.startswith("https://spec.industrialontologies.org/ontology/construct/"):
            fails.append("IRI 前缀不符合 IOF construct 命名空间")

    coverage = round(len(seen) / len(local), 3) if local else 0
    if coverage < 0.6:
        fails.append("覆盖率 %s < 0.6" % coverage)

    print("映射 %d 条，本地类 %d 个，覆盖率 %.1f%%" % (len(seen), len(local), coverage * 100))
    for f in fails:
        print("FAIL", f)
    print("通过" if not fails else "不通过")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
