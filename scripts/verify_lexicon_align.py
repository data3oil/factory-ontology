#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_lexicon_align.py — 词典碎片与跨库对齐检查门（纯标准库，默认不依赖外部工具）。

为什么要有它
------------
每个 KB 一份 `config/lexicon_<kb>.json`，是"中文问法 → 英文列"的桥。这层桥一旦错，
问答就不是"答得慢"，而是"答到不存在的表上"，属于静默错误，最危险。

实测到的三类碎片（本门就是拿它们当输入设计的）：
  1. **孤儿词典**：`config/` 下有 lexicon，但 `kbs.json` 没注册（历史测试残留）。
     后果：改错库、跑错配置时无人察觉。
  2. **实体名错配**：`entity_cn2en` 里的中文指向本库根本不存在的表。
     典型成因是从别的库复制词典没改干净（chem 词典指向 mach_*、valve 词典里有 supplier）。
     后果：问"阀门供应商是谁"，映射到一个不存在的表，只能答空或乱答。
  3. **同键/近义冲突**：归一化后同一个中文词映射到不同列；或"产品名"与"产品"
     分别映射 product_name / product_id —— 全名与 ID 混淆，用户问"产品"到底给哪个。

检查分两档
----------
  结构性错误（error，导致退出码 1）
      · 孤儿词典 / 注册库缺词典
      · 实体名指向不存在的表
      · 属性名指向不存在的列（且不是跨表通用列）
      · 同一中文键映射多个不同列（归一化后）
  建议（warn，不阻断）
      · 近义中文键（编辑距离 ≤1）映射到不同列 → 疑似全名/ID 混淆
      · 同一英文列在不同库的中文名不一致 → 跨库未对齐
      · 白名单里的条目一律跳过（`scripts/lexicon_align_allow.json`）

外部对齐通道（可选）
--------------------
本门默认纯标准库（编辑距离 + 归一化 + 白名单）。若装了 OntoAligner 之类的外部工具，
可用 `--aligner cmd:<命令模板>` 把"近义候选"交给它复核，模板里 `{a}` `{b}` 会被替换成
两个中文词，输出含 `same` 即视为同义。**外部通道失败一律降级为内置判定，不阻断门。**
这样做是为了不把零依赖红线破掉：能力可以增，依赖不能硬。

用法
----
  python scripts/verify_lexicon_align.py                 # 全库检查
  python scripts/verify_lexicon_align.py --kb valve      # 单库
  python scripts/verify_lexicon_align.py --verbose       # 打印每条明细
  python scripts/verify_lexicon_align.py --json out.json # 落盘完整报告
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
CFG = os.path.join(CODES, "config")
ALLOW_FILE = os.path.join(ROOT, "scripts", "lexicon_align_allow.json")

# 跨表通用列：允许出现在任何词典的属性映射里（审计/元数据类）
_COMMON_COLS = {"id", "name", "status", "type", "category", "remark", "note",
                "created_at", "updated_at", "date", "time", "quantity", "price"}


def _norm(s):
    """中文词归一：去括号后缀、全半角、空白与标点。'口径(mm)'→'口径'。"""
    s = str(s or "")
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = s.replace("（", "").replace("）", "")
    s = re.sub(r"[\s_\-/·、,，.。]+", "", s)
    return s.strip().lower()


def _canon(col):
    """列/表名归一，与问答引擎同一口径（见 ontology_qa_v3 `_canon`/attrs 取值）。

    引擎会把 `device_type` 与 `deviceType` 视为同一列（拆词后拼 camel，再按去下划线
    小写兜底），所以本门判"列名是否存在"必须用同样的归一，否则会把引擎本来支持的
    写法误判成错配 —— 那是假阳性，会让门失去可信度。
    """
    return re.sub(r"[_\-]", "", str(col or "")).lower()


def _ed1(a, b):
    """编辑距离是否 ≤1（只做判定，不返回距离，省一次 DP）。"""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = j = diff = 0
    while i < len(s) and j < len(l):
        if s[i] != l[j]:
            diff += 1
            if diff > 1:
                return False
            j += 1
        else:
            i += 1
            j += 1
    return True


def _canon_set(names):
    """把一组列名/表名归一成集合，供存在性判定（兼容 snake_case 与 camelCase）。"""
    return {_canon(x) for x in names}


def _tables(kb):
    d = os.path.join(CODES, "data_%s" % kb)
    return {os.path.basename(p)[:-4] for p in glob.glob(os.path.join(d, "*.csv"))}


def _table_cols(table_path):
    try:
        with open(table_path, encoding="utf-8-sig") as f:
            head = f.readline().strip()
        return [c.strip().strip('"') for c in head.split(",")]
    except Exception:
        return []


def _cols(kb):
    out = {}
    for p in glob.glob(os.path.join(CODES, "data_%s" % kb, "*.csv")):
        out[os.path.basename(p)[:-4]] = set(_table_cols(p))
    return out


def _load_allow():
    if not os.path.exists(ALLOW_FILE):
        return {"entities": [], "terms": [], "notes": "允许的已知差异"}
    try:
        with open(ALLOW_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entities": [], "terms": []}


def _allowed(allow, kind, kb, value):
    for row in allow.get(kind) or []:
        if row.get("kb") in (kb, "*") and row.get("value") == value:
            return True
    return False


def check_kb(kb, lex, kbs, allow, aligner=None, verbose=False):
    errs, warns = [], []
    tables = _tables(kb)
    cols = _cols(kb)
    # 列名全集：cols 是 {表: {列}}，取并集才是"本库有哪些列"。
    # 早前直接 _canon_set(cols) 拿到的是表名集合，导致 entity→列名（如 supplier）被误报。
    allcol = set().union(*cols.values()) if cols else set()

    # 1. 实体名 → 表（也允许指向列：`供应商` 这类在库内是某表的列而非独立表）
    for cn, en in (lex.get("entity_cn2en") or {}).items():
        if not en:
            continue
        if not tables:
            break  # 该库无 CSV，跳过（如 ai4i 的合成表名）
        if _canon(en) not in _canon_set(tables) and _canon(en) not in _canon_set(allcol):
            if _allowed(allow, "entities", kb, en):
                continue
            errs.append(("E2 实体名指向不存在的表", "%s → %s（本库表：%s）"
                         % (cn, en, "、".join(sorted(tables)[:6]))))

    # 2. 属性名 → 列
    for cn, en in (lex.get("attr_cn2en") or {}).items():
        if not en or en in _COMMON_COLS:
            continue
        if not allcol:
            break
        if _canon(en) not in _canon_set(allcol):
            if _allowed(allow, "terms", kb, en):
                continue
            errs.append(("E3 属性名指向不存在的列", "%s → %s" % (cn, en)))

    # 3. 归一化后同键多值
    seen = {}
    for cn, en in (lex.get("attr_cn2en") or {}).items():
        k = _norm(cn)
        if not k:
            continue
        seen.setdefault(k, set()).add(en)
    for k, ens in sorted(seen.items()):
        if len(ens) > 1:
            errs.append(("E4 同一中文词映射多个列", "%s → %s" % (k, "、".join(sorted(ens)))))

    # 4. 近义键映射不同列（全名/ID 混淆高危）
    keys = [k for k in seen if k]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            if not _ed1(a, b) or seen[a] == seen[b]:
                continue
            pair = (a, b)
            if _allowed(allow, "terms", kb, "%s|%s" % pair):
                continue
            same = _external_same(aligner, a, b) if aligner else None
            if same is False:
                continue
            warns.append(("W1 近义中文词映射到不同列", "%s(%s) vs %s(%s)%s"
                          % (a, "、".join(sorted(seen[a])), b, "、".join(sorted(seen[b])),
                             "  [外部判定同义]" if same else "")))
    return errs, warns


def _external_same(aligner, a, b):
    """可选外部对齐通道。任何失败都返回 None（降级为内置判定，不阻断）。"""
    if not aligner or not aligner.startswith("cmd:"):
        return None
    tpl = aligner[4:]
    try:
        cmd = [p.replace("{a}", a).replace("{b}", b) for p in tpl.split()]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, encoding="utf-8", errors="replace", timeout=20)
        out = (p.stdout or "").lower()
        if "not_same" in out or "different" in out:
            return False
        if "same" in out:
            return True
    except Exception:
        return None
    return None


def cross_kb_warn(lexs, kbs):
    """同一英文列在不同库的中文名不一致 → 跨库未对齐（只提示）。"""
    en2cn = {}
    for kb in kbs:
        lex = lexs.get(kb) or {}
        for cn, en in (lex.get("attr_cn2en") or {}).items():
            if en in _COMMON_COLS:
                continue
            en2cn.setdefault(en, {}).setdefault(_norm(cn), set()).add(kb)
    out = []
    for en, cnmap in sorted(en2cn.items()):
        if len(cnmap) < 2:
            continue
        names = sorted(cnmap.keys())
        out.append((en, {n: sorted(cnmap[n]) for n in names}))
    return out


# 已知的"库名前缀"：词典里出现 `mach_products` / `internal_equipment` 这类写法时，
# 去掉前缀后的名称才是本库真正要找的列/表。
_LIB_PREFIXES = {"internal", "mach", "valve", "food", "chem", "seis", "ship", "eco",
                 "tpl", "library", "auto", "public", "demo", "power", "factory"}

# 孤儿 = "任何 kbs*.json 都没注册"，但**没注册不等于没人用**。
# 这份名单里的词典都有一条可核实的引用，归档会让某条路径断掉：
#   factory         run.py 的 setup_schema(table="factory") 默认值，按 lexicon_{table}.json 读写
#   food_products   benchmark_graphrag.py / benchmark_logical.py 的 --lexicon 默认值；
#                   api_server.py 的 FOOD_LEX 兜底
#   valve_products  valve_demo.py 里硬编码的 bench_lex 路径
#   precision       有 codes/data_precision/ 数据目录（未注册的建库试作，不是碎片）
# 判定依据只能是这样可核实的引用，不能凭"名字像临时文件"。
_KEEP_ORPHANS = {
    "factory": "run.py 默认 table=factory，建库流程按 lexicon_{table}.json 读写",
    "food_products": "benchmark_graphrag/logical 的 --lexicon 默认值、api_server 的 fallback",
    "valve_products": "valve_demo.py 硬编码路径",
    "precision": "有 codes/data_precision/ 数据目录（未注册的建库试作）",
}


def _remap_candidate(en, names, kb=None):
    """给一个"本库不存在的表/列名"找同义项。只在**去掉库名前缀后精确相等**时采纳。

    为什么不按尾段匹配：`payment_status` 的尾段也是 `status`，但那把付款状态连到设备
    状态上，是错的。只有"前缀 + 精确余名"这种可解释的对应才允许自动改，其余交人工。
    """
    parts = str(en).split("_")
    if len(parts) < 2 or parts[0].lower() not in _LIB_PREFIXES:
        return None
    stripped = "_".join(parts[1:])
    want = {_canon(stripped)}
    if kb:
        want.add(_canon(str(kb) + "_" + stripped))   # chem_products 这种"库名_余名"
    hits = [t for t in sorted(names) if _canon(t) in want]
    return hits[0] if len(hits) == 1 else None


def prune_kb(kb, lex, dry=False):
    """修词典里指向本库不存在的表/列的条目：能重映射就重映射，不能就停用留证。

    为什么不直接删：删掉就丢了"曾经映射到哪"的证据，回溯与审阅都无据可查。
    停用 = 移到 `_disabled_by_align` 段并写明原因；引擎不读该段，等于止错但仍可逆。

    只动 `entity_cn2en` / `attr_cn2en` 两张映射。值映射（status/type 等）不碰：
    那是"取值别名"，短值如 alarm/运行中 跨库通用，误删风险大于收益。
    """
    tables = _tables(kb)
    cols = _cols(kb)
    if not tables and not cols:
        print("跳过 %s：无 CSV 数据，无法判定列是否存在" % kb)
        return 0
    canon_t = _canon_set(tables)
    allcols = set().union(*cols.values()) if cols else set()
    canon_c = _canon_set(allcols)
    disabled = lex.setdefault("_disabled_by_align", [])
    remaps, disables = [], []

    ent = lex.get("entity_cn2en") or {}
    for cn, en in list(ent.items()):
        if not en or _canon(en) in canon_t or _canon(en) in canon_c:
            continue
        alt = _remap_candidate(en, tables, kb)
        if alt:
            ent[cn] = alt
            remaps.append(("entity_cn2en", cn, en, alt))
        else:
            ent.pop(cn)
            disabled.append({"map": "entity_cn2en", "cn": cn, "was": en,
                             "reason": "本库无此表：%s" % "、".join(sorted(tables)[:6])})
            disables.append(("entity_cn2en", cn, en))

    attr = lex.get("attr_cn2en") or {}
    for cn, en in list(attr.items()):
        if not en or en in _COMMON_COLS or _canon(en) in canon_c:
            continue
        alt = _remap_candidate(en, allcols, kb)
        if alt:
            attr[cn] = alt
            remaps.append(("attr_cn2en", cn, en, alt))
        else:
            attr.pop(cn)
            disabled.append({"map": "attr_cn2en", "cn": cn, "was": en, "reason": "本库无此列"})
            disables.append(("attr_cn2en", cn, en))

    n = len(remaps) + len(disables)
    if not n:
        print("[%s] 无需修复" % kb)
        return 0
    print("[%s] 重映射 %d / 停用 %d" % (kb, len(remaps), len(disables)))
    for m, cn, old, new in remaps:
        print("    ~ %-12s %s: %s → %s" % (m, cn, old, new))
    for m, cn, old in disables[:8]:
        print("    - %-12s %s: %s（停用留证）" % (m, cn, old))
    if dry:
        return n

    p = os.path.join(CFG, "lexicon_%s.json" % kb)
    bak = p + ".bak"
    if not os.path.exists(bak):
        with open(bak, "w", encoding="utf-8", newline="\n") as f:
            json.dump(lex, f, ensure_ascii=False, indent=2)
    rev = lex.get("attr_en2cn") or {}
    for _m, cn, _old, new in remaps:
        if new:
            rev.setdefault(new, cn)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(lex, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", default=None, help="把完整报告写到该路径")
    ap.add_argument("--aligner", default=None,
                    help="可选外部对齐命令，如 'cmd:python my_align.py {a} {b}'")
    ap.add_argument("--prune", action="store_true",
                    help="把指向不存在的表/列的条目从词典里删掉（先备份 .bak，打印清单）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --prune/--archive-orphans，只打印不改文件")
    ap.add_argument("--archive-orphans", action="store_true",
                    help="把未注册的孤儿词典移到 config/_lexicon_archive/（不删除，可随时搬回）")
    a = ap.parse_args()

    # 注册来源：正式 kbs.json + 开发 kbs.dev.json（存在的都算）。
    # 只认 kbs.json 会把 dev 库误判成"孤儿词典" —— 那是开发态在用的库，不是碎片。
    kbs, srcs = [], []
    for name in ("kbs.json", "kbs.dev.json"):
        p = os.path.join(CFG, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                got = list(json.load(f).get("kbs", {}).keys())
            kbs.extend(got)
            srcs.append((name, len(got)))
        except Exception as e:
            print("[warn] %s 解析失败：%s" % (name, e))
    kbs = sorted(set(kbs))
    print("注册来源：%s" % "、".join("%s(%d)" % t for t in srcs))
    lexs = {}
    for p in sorted(glob.glob(os.path.join(CFG, "lexicon_*.json"))):
        kb = os.path.basename(p)[len("lexicon_"):-len(".json")]
        try:
            with open(p, encoding="utf-8") as f:
                lexs[kb] = json.load(f)
        except Exception as e:
            print("[warn] 词典解析失败 %s: %s" % (p, e))

    allow = _load_allow()
    report = {"errors": [], "warns": [], "orphans": [], "missing": [], "cross_kb": []}

    # 1. 注册完整性
    orphans = sorted(set(lexs) - set(kbs))
    missing = sorted(set(kbs) - set(lexs))
    report["orphans"], report["missing"] = orphans, missing
    # 注册卫生问题记为 warn：开发态试错残留很常见，不该把 CI 卡死；
    # 但只要真被加载就会出错（问不存在的库），所以必须显式可见、可查。
    for kb in orphans:
        report["warns"].append((kb, "W0 孤儿词典", "lexicon_%s.json 存在但任何 kbs*.json 都未注册" % kb))
    for kb in missing:
        report["warns"].append((kb, "W0 注册库缺词典", "已注册 %s，但没有对应 lexicon 文件" % kb))

    targets = [a.kb] if a.kb else sorted(set(kbs) & set(lexs))

    if a.archive_orphans:
        dst = os.path.join(CFG, "_lexicon_archive")
        moved = 0
        kept = []
        for kb in orphans:
            src = os.path.join(CFG, "lexicon_%s.json" % kb)
            if not os.path.exists(src):
                continue
            if kb in _KEEP_ORPHANS:
                kept.append((kb, _KEEP_ORPHANS[kb]))
                continue
            if a.dry_run:
                print("将归档 %s" % os.path.basename(src))
                moved += 1
                continue
            os.makedirs(dst, exist_ok=True)
            os.replace(src, os.path.join(dst, os.path.basename(src)))
            moved += 1
        for kb, why in kept:
            print("保留 %s：%s" % (os.path.basename("lexicon_%s.json" % kb), why))
        if moved and not a.dry_run:
            note = os.path.join(dst, "README.md")
            if not os.path.exists(note):
                with open(note, "w", encoding="utf-8", newline="\n") as f:
                    f.write("# 已归档的孤儿词典\n\n"
                            "这些 `lexicon_*.json` 未被 `codes/config/kbs.json` 注册，"
                            "多为历史测试/临时建模产物。\n"
                            "归档而非删除：需要时把文件搬回 `codes/config/` 并在 kbs.json 注册即可。\n")
            # 同步把 .bak 也归档，避免目录噪声
            for kb in orphans:
                if kb in _KEEP_ORPHANS:
                    continue
                b = os.path.join(CFG, "lexicon_%s.json.bak" % kb)
                if os.path.exists(b):
                    os.replace(b, os.path.join(dst, os.path.basename(b)))
        print("%s %d 份孤儿词典 → %s" % ("将归档" if a.dry_run else "已归档", moved, dst))
        return 0

    if a.prune:
        total = 0
        for kb in targets:
            if kb not in lexs:
                print("跳过 %s：无词典" % kb)
                continue
            total += prune_kb(kb, lexs[kb], dry=a.dry_run)
        print("\n%s %d 条" % ("将删除" if a.dry_run else "已删除", total))
        return 0

    for kb in targets:
        errs, warns = check_kb(kb, lexs.get(kb) or {}, kbs, allow, a.aligner, a.verbose)
        for rule, detail in errs:
            report["errors"].append((kb, rule, detail))
        for rule, detail in warns:
            report["warns"].append((kb, rule, detail))

    if not a.kb:
        for en, cnmap in cross_kb_warn(lexs, [k for k in kbs if k in lexs]):
            report["cross_kb"].append((en, cnmap))

    # 输出
    by_rule = {}
    for kb, rule, _d in report["errors"]:
        by_rule[rule] = by_rule.get(rule, 0) + 1
    print("词典 %d 份 / 注册库 %d 个" % (len(lexs), len(kbs)))
    if report["orphans"]:
        print("  孤儿词典 %d：%s" % (len(report["orphans"]), "、".join(report["orphans"][:10])))
    if report["missing"]:
        print("  注册库缺词典 %d：%s" % (len(report["missing"]), "、".join(report["missing"])))
    print("  结构性错误 %d：%s" % (len(report["errors"]), by_rule or "无"))
    print("  注册卫生：孤儿 %d / 缺词典 %d（warn，见 --verbose）"
          % (len(report["orphans"]), len(report["missing"])))
    print("  建议 %d（不阻断）" % len(report["warns"]))

    if a.verbose:
        for kb, rule, detail in report["errors"]:
            print("  [ERR] %-8s %-24s %s" % (kb, rule, detail))
        for kb, rule, detail in report["warns"]:
            print("  [WARN]%-8s %-24s %s" % (kb, rule, detail))
        if report["cross_kb"]:
            print("  跨库未对齐 %d 个英文列：" % len(report["cross_kb"]))
            for en, cnmap in report["cross_kb"][:12]:
                print("    %-18s %s" % (en, " / ".join("、".join(v[:2]) for v in cnmap.values())))

    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"summary": {"lexicons": len(lexs), "kbs": len(kbs),
                                   "errors": len(report["errors"]), "warns": len(report["warns"]),
                                   "by_rule": by_rule},
                       "errors": [{"kb": k, "rule": r, "detail": d} for k, r, d in report["errors"]],
                       "warns": [{"kb": k, "rule": r, "detail": d} for k, r, d in report["warns"]],
                       "cross_kb": [{"column": en, "names": {n: v for n, v in cnmap.items()}}
                                    for en, cnmap in report["cross_kb"]]},
                      f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("  报告 → %s" % a.json)

    if report["errors"]:
        print("FAIL：结构性错误 %d 条（见 --verbose 明细）" % len(report["errors"]))
        return 1
    print("PASS：无结构性错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
