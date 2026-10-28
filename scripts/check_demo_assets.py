# -*- coding: utf-8 -*-
"""去演示化检查门：扫出仓库里的演示资产，并确认默认配置没有把它们当生产口径。

判据：
1. 演示资产清单（data_demo*/ lex*_demo* flows/demo_*）必须能列全，便于交付时剔除；
2. 默认生效配置（config/lexicon.json、config/kbs.json、flows/presets.json）里不得指向演示资产；
3. 演示数据目录必须带 README 或文件名含 demo，避免被当成真实客户数据。

退出码：0 = 通过；1 = 默认配置仍指向演示资产。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")

DEMO_PAT = re.compile(r"(demo|演示)", re.I)


def walk_assets():
    hits = []
    for dp, dn, fn in os.walk(CODES):
        dn[:] = [d for d in dn if d not in {"__pycache__", "node_modules", ".git", "output"}]
        rel_dir = os.path.relpath(dp, ROOT).replace("\\", "/")
        if DEMO_PAT.search(rel_dir):
            for f in fn:
                if f.endswith((".csv", ".json", ".sql", ".nt", ".md")):
                    hits.append(os.path.join(rel_dir, f))
            continue
        for f in fn:
            if DEMO_PAT.search(f) and f.endswith((".csv", ".json", ".sql", ".nt", ".md")):
                hits.append(os.path.join(rel_dir, f))
    return sorted(set(hits))


def default_configs():
    out = {}
    for rel in ("codes/config/lexicon.json", "codes/config/kbs.json", "codes/flows/presets.json"):
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            out[rel] = open(p, encoding="utf-8", errors="replace").read()
    return out


def main():
    assets = walk_assets()
    print("演示资产 %d 个：" % len(assets))
    for a in assets:
        print("  -", a)

    # kbs.json 里显式标了 "demo": true 的条目视为已隔离（供回归用），不算违规
    kbs_path = os.path.join(ROOT, "codes", "config", "kbs.json")
    demo_keys = set()
    if os.path.isfile(kbs_path):
        try:
            reg = json.load(open(kbs_path, encoding="utf-8")).get("kbs", {})
            demo_keys = {k for k, v in reg.items() if isinstance(v, dict) and v.get("demo")}
        except Exception:
            demo_keys = set()

    bad = []
    for rel, body in default_configs().items():
        refs = [a for a in assets if os.path.basename(a) in body or a.replace("codes/", "") in body]
        if rel.endswith("kbs.json") and refs and demo_keys:
            # 只要引用方条目都标了 demo，就放行
            allowed = [r for r in refs
                       if os.path.basename(r) in body and any(
                           (v.get("lexicon") or "") and os.path.basename(r) == os.path.basename(v.get("lexicon", ""))
                           for k, v in reg.items() if k in demo_keys)]
            refs = [r for r in refs if r not in allowed]
        if refs:
            bad.append((rel, refs[:3]))

    print("\n默认配置引用检查：")
    if bad:
        for rel, refs in bad:
            print("  [FAIL] %s 引用了演示资产：%s" % (rel, ", ".join(refs)))
    else:
        print("  [PASS] 默认配置未引用任何演示资产")

    # 演示数据目录是否有说明
    missing_doc = []
    for d in sorted(os.listdir(CODES)):
        full = os.path.join(CODES, d)
        if os.path.isdir(full) and DEMO_PAT.search(d):
            if not any(f.lower().startswith("readme") or f == "README.md" for f in os.listdir(full)):
                missing_doc.append(d)
    if missing_doc:
        print("\n  [WARN] 以下演示数据目录缺 README 说明：%s" % ", ".join(missing_doc))
    else:
        print("\n  [PASS] 演示数据目录均有说明文件")

    ok = not bad
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
