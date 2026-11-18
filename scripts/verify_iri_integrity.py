#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_iri_integrity.py — 本体导出件的 IRI 完整性回归门（纯标准库）。

治的是历史上真实发生过、修好后容易复发的三类导出缺陷（见 docs/本体公理集与小型推理机-20261110.md:85-93）：
  · 个体 IRI 用了引用方表前缀，导致对象边全部悬空（销售→产品曾 30 条全断）
  · 对象属性 domain/range 写错（sells 两边都写成 ValveSales）
  · 域类/父类中文局部名直接进 IRI（`o:设备域`），Turtle 不可移植

检查项（逐库导出后静态判定）：
  I1 悬空引用：本命名空间的 IRI 宾语必须作为主体出现过（有类型或定义）
  I2 域值域：owl:ObjectProperty 的 rdfs:domain/range 必须指向已声明的 owl:Class
  I3 局部名安全：本命名空间全部 IRI 的局部名必须落在 ASCII 安全子集
  I4 前缀一致：导出文本里本命名空间不应残留未缩略的 `<ns...>` 写法

用法：
  python scripts/verify_iri_integrity.py                  # 扫 config 下所有可配对数据的 schema
  python scripts/verify_iri_integrity.py --schema X --data Y
退出码：0 = 全过；1 = 有断言未过。
"""
import argparse
import glob
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, CODES)
import ontology_lint_lib as L  # noqa: E402


# 默认只跑这几个有代表性的行业库：全量 20+ 库里有百万行级台账，导出一次要好几分钟，
# 不适合做每次提交都跑的门。需要全量时加 --all。
CORE_KBS = ["valve", "food_co", "chem", "ai4i", "auto_parts"]


def _pairs(all_kbs=False):
    """config 下每个 ontology_schema_<name>.json，若能找到 data_<name> 就配对。"""
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


def check_text(text, ns, base_subs=None):
    """对一份 Turtle 文本做 I1–I3 判定，返回 [(规则, 明细)]。

    base_subs: 外部已知主体集合（如 TBox 的类/属性声明）。shapes.ttl / ontology-full.ttl
    自身不含全部 TBox 声明，必须并上 ontology.ttl 的声明集合，否则会把合法引用误判为悬空。
    """
    pf, tri = L.parse_turtle(text)
    errs = []
    subs = L.subject_iris(tri) | set(base_subs or ())
    all_cls = L.classes(tri)

    # I1 悬空引用（跳过本体 IRI 自身与外部词汇）
    for o in sorted(L.object_iris(tri)):
        if o.startswith(ns) and o != ns and o not in subs:
            errs.append(("I1 悬空引用", L.local_name(o)))

    # I2 域值域必须落在已声明类上
    for s, p, o, _sk, ok in tri:
        if p in (L.RDFS_DOMAIN, L.RDFS_RANGE) and ok == "iri" and o.startswith(ns):
            if o not in all_cls:
                errs.append(("I2 域值域指向未声明类", "%s -> %s" % (L.local_name(s), L.local_name(o))))

    # I3 局部名 ASCII 安全
    bad = set()
    for x in subs | L.object_iris(tri):
        if x.startswith(ns) and x != ns and not L.is_safe_local(x):
            bad.add(L.local_name(x))
    for b in sorted(bad):
        errs.append(("I3 非 ASCII 局部名", b))
    return errs


def check_export(outdir, ns):
    """检查一次导出的产物。

    悬空/域值域以 ontology.ttl（TBox 声明全集）为基准，shapes.ttl 与 ontology-full.ttl
    只补自身新增主体，避免"引用合法但未在本文件声明"的误报。
    """
    errs = []
    tbox = os.path.join(outdir, "ontology.ttl")
    tbox_subs, tbox_cls = set(), set()
    if os.path.exists(tbox):
        with open(tbox, encoding="utf-8") as f:
            _pf, _tri = L.parse_turtle(f.read())
        tbox_subs = L.subject_iris(_tri)
        tbox_cls = L.classes(_tri)

    for fn in ("ontology.ttl", "shapes.ttl", "ontology-full.ttl"):
        p = os.path.join(outdir, fn)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            text = f.read()
        base = None if fn == "ontology.ttl" else (tbox_subs | tbox_cls)
        for rule, detail in check_text(text, ns, base_subs=base):
            errs.append((fn, rule, detail))
        # I4 前缀一致：本命名空间不应以 `<ns...>` 裸写法出现（应缩略为前缀）
        # 排除 @prefix 声明行本身——那行就是 ns 的定义，属于合法写法。
        hits = []
        for ln in text.splitlines():
            if ln.strip().startswith("@prefix"):
                continue
            hits.extend(re.findall(r"<" + re.escape(ns) + r"[^>]*>", ln))
        if hits:
            errs.append((fn, "I4 前缀不一致", "%d 处本命名空间 IRI 未缩略（例 %s）"
                         % (len(hits), hits[0][:70])))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema")
    ap.add_argument("--data")
    ap.add_argument("--all", action="store_true", help="全量库（慢，默认只跑核心 5 库）")
    a = ap.parse_args()
    import ontology_export as oe
    import schema_ontology as so

    runs = []
    if a.schema and a.data:
        runs = [("custom", a.schema, a.data)]
    else:
        runs = _pairs(all_kbs=a.all)

    if not runs:
        print("自检结果: 没有可配对的数据目录，跳过（0 库）")
        return 0

    bad_total = 0
    for name, sp, data in runs:
        with tempfile.TemporaryDirectory(prefix="iri_") as td:
            oe.export(sp, td, data)
            schema = so.load_schema(sp)
            m = schema.get("_ns") or so.resolve_namespace(schema)
            ns = m["ns"]
            errs = check_export(td, ns)
        if errs:
            bad_total += 1
            print("[FAIL] %-16s %d 处问题" % (name, len(errs)))
            for fn, rule, detail in errs[:10]:
                print("         %-22s %-10s %s" % (fn, rule, detail))
            if len(errs) > 10:
                print("         ... 另有 %d 处" % (len(errs) - 10))
        else:
            print("[PASS] %-16s 悬空0 域值域0 非ASCII0" % name)

    print("\n自检结果: %s（%d 库，%d 库有问题）"
          % ("通过" if bad_total == 0 else "不通过", len(runs), bad_total))
    return 0 if bad_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
