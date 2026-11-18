#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suggest_types.py — 术语类型化与层次建议（只出候选，人拍板后才进 schema）。

为什么要有它
------------
现在建本里唯一被自动提升成"类型类"的只有 `category` 一列（见 `_nt_category_hierarchy`）。
但真实的制造数据里，压力等级、连接形式、密封材质、质检结论、储存条件……都是**类型维度**，
它们同属"某属性取值有限且重复出现"的同一类语义结构。只认 category 等于把大部分类型
信息留在字面量里，问"压力等级有哪几种"就只能靠字符串匹配，靠不住。

同时，自动猜"这列是类型"很容易猜错（supplier/checker 值域也小，但那是实体引用，不是类型）。
所以本脚本的定位是 **建议器**，不是 **决策器**：

  · 规则先出候选，每条带 evidence（哪张表、哪列、几个值、命中多少行、为什么判成类型维度）；
  · LLM 可选（`--llm`），只用来补中文标签/语义说明，**不改变候选集**，失败静默降级；
  · 默认不写 schema。人审阅后把要用的条目 `approved` 改成 true，再 `--apply` 落库。

候选类型（kind）
----------------
  dimension      属性取值域 → 类型类（建议建 `XxxType` 与枚举成员）
  named_subclass 复用 derive_named_subclasses：枚举值 → 具名子类（IS-A，带实例归类）
  hierarchy_hint 实体命名同词根 → 建议归入共同父类（只提示，不自动改层次）

用法
----
  python scripts/suggest_types.py --kb valve --out scripts/type_suggestions_valve.json
  python scripts/suggest_types.py --kb valve --llm local        # 补中文标签（可选）
  python scripts/suggest_types.py --kb valve --apply scripts/type_suggestions_valve.json
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
if CODES not in sys.path:
    sys.path.insert(0, CODES)

# 列名末段 → 类型维度（强/弱两档）。强档直接 high，弱档需再看值域。
_DIM_STRONG = {
    "type", "category", "class", "grade", "level", "kind", "status", "result",
    "state", "quality_status", "delivery_status", "credit_level", "pressure_grade",
    "connection", "packaging", "storage", "device_type", "product_type",
}
# 值域小但更可能是"实体引用/人员"，不应当成类型维度；只做低置信提示
_DIM_WEAK = {"supplier", "checker", "maintainer", "team", "workshop", "region",
             "industry", "material", "seal_material", "body_material", "standard_no"}

_MIN_VALUES, _MAX_VALUES = 2, 15
_MIN_FILL = 0.9          # 非空率
_MIN_REPEAT = 1.5        # 平均重复率 = 行数 / 值域大小


def _load(kb):
    import schema_ontology as so
    sp = os.path.join(CODES, "config", "ontology_schema_%s.json" % kb)
    dp = os.path.join(CODES, "data_%s" % kb)
    if not os.path.exists(sp):
        raise SystemExit("找不到 schema: %s" % sp)
    schema = so.load_schema(sp)
    data = so.load_all(dp)
    return schema, data


def _attr_cn_map(kb):
    """从该库词典取 列名→中文名。词典是仓库既有资产，比 LLM 更稳、可离线。"""
    if not kb:
        return {}
    p = os.path.join(CODES, "config", "lexicon_%s.json" % kb)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            lex = json.load(f)
        return {str(k): str(v) for k, v in (lex.get("attr_en2cn") or {}).items()}
    except Exception:
        return {}


def _col_profile(rows, col):
    vals = [str(r.get(col)).strip() for r in rows if r.get(col) not in (None, "")]
    distinct = sorted(set(vals))
    return {
        "n": len(rows),
        "filled": len(vals),
        "distinct": distinct,
        "fill_rate": (len(vals) / len(rows)) if rows else 0.0,
        "repeat": (len(vals) / len(distinct)) if distinct else 0.0,
    }


def _fk_cols(schema):
    out = set()
    for r in schema.get("relations", []):
        fk = r.get("fk") or ""
        if "." in fk:
            t, c = fk.split("=", 1)[0].split(".", 1)
            out.add((t, c))
    return out


def suggest(schema, data, use_llm=None, kb=None):
    """返回候选列表（确定性排序：entity asc, column asc）。纯读，不改 schema。"""
    fks = _fk_cols(schema)
    cn = _attr_cn_map(kb)
    cands = []

    # ── 1. 类型维度候选 ──────────────────────────────────────────────
    for ent in schema.get("entities", []):
        if ent.get("kind") == "subclass" or not ent.get("table"):
            continue
        table = ent["table"]
        rows = data.get(table) or []
        if not rows:
            continue
        key = ent.get("key")
        for col in rows[0].keys():
            if col == key or (table, col) in fks:
                continue
            tail = col.lower()
            hit_strong = tail in _DIM_STRONG or tail.split("_")[-1] in _DIM_STRONG
            hit_weak = tail in _DIM_WEAK or tail.split("_")[-1] in _DIM_WEAK
            if not (hit_strong or hit_weak):
                continue
            p = _col_profile(rows, col)
            k = len(p["distinct"])
            if not (_MIN_VALUES <= k <= _MAX_VALUES):
                continue
            if p["fill_rate"] < _MIN_FILL or p["repeat"] < _MIN_REPEAT:
                continue
            if hit_strong:
                conf, why = "high", "列名末段命中类型维度词表，且取值域 %d 个、平均重复 %.1f 次" % (k, p["repeat"])
            else:
                conf, why = "low", ("列名像实体引用（%s），值域 %d 个；若确认是分类值再采纳，"
                                    "否则应建为独立实体" % (col, k))
            cands.append({
                "kind": "dimension",
                "entity": ent["id"],
                "table": table,
                "column": col,
                "label": (ent.get("label") or ent["id"]) + "-" + (cn.get(col) or col),
                "column_cn": cn.get(col) or "",
                "values": p["distinct"],
                "confidence": conf,
                "evidence": "表 %s 列 %s：%d/%d 行非空，%d 个取值，平均重复 %.1f 次。%s"
                            % (table, col, p["filled"], p["n"], k, p["repeat"], why),
                "approved": False,
            })

    # ── 2. 具名子类候选（复用既有派生器，避免两套口径）─────────────────
    try:
        import schema_ontology as so
        for sc in so.derive_named_subclasses(schema, data):
            cands.append({
                "kind": "named_subclass",
                "entity": sc["parent"],
                "table": next((e.get("table") for e in schema.get("entities", [])
                               if e.get("id") == sc["parent"]), None),
                "column": sc.get("column"),
                "label": sc.get("label"),
                "value": sc.get("value"),
                "values": [sc.get("value")],
                "confidence": "high",
                "evidence": sc.get("evidence", ""),
                "suggestion": {"name": sc.get("name"), "parent": sc.get("parent"),
                               "definition": sc.get("definition")},
                "approved": False,
            })
    except Exception as e:  # 派生器异常不阻断类型建议
        print("[warn] derive_named_subclasses 失败：%s" % e, file=sys.stderr)

    # ── 3. 层次提示：实体名共享词根 ────────────────────────────────────
    groups = {}
    for ent in schema.get("entities", []):
        if ent.get("kind") == "subclass":
            continue
        head = re.split(r"[_\W]+", ent["id"])[0].lower()
        if head:
            groups.setdefault(head, []).append(ent["id"])
    for head, ids in sorted(groups.items()):
        if len(ids) < 2:
            continue
        cands.append({
            "kind": "hierarchy_hint",
            "entity": ids[0],
            "entities": sorted(ids),
            "label": head,
            "confidence": "low",
            "evidence": "%d 个实体共享词根 %s：%s。若它们确属同一上位概念，可加共同父类；"
                        "仅是命名前缀则忽略。" % (len(ids), head, "、".join(sorted(ids))),
            "approved": False,
        })

    # ── 4. LLM 可选：只补中文标签，不增删候选 ──────────────────────────
    if use_llm:
        _fill_labels_with_llm(cands, use_llm)

    cands.sort(key=lambda c: (c["kind"], str(c.get("entity")), str(c.get("column"))))
    return cands


def _fill_labels_with_llm(cands, model_key):
    """用大模型为 dimension 候选补中文短语。失败静默（保持确定性优先）。"""
    dims = [c for c in cands if c["kind"] == "dimension"]
    if not dims:
        return
    try:
        import model_llm as ml
        prompt = ("下面是制造业数据表的若干分类列，请为每列给出简短中文名（2-6 字，如 "
                  "pressure_grade→压力等级）。只输出 JSON 数组，元素为 "
                  '{"column":"列名","cn":"中文名"}，不要解释。\n'
                  "列名列表：" + json.dumps([c["column"] for c in dims], ensure_ascii=False))
        txt = ml.chat(prompt, model_key=model_key) if hasattr(ml, "chat") else ""
        m = re.search(r"\[.*\]", txt or "", re.S)
        if not m:
            return
        for row in json.loads(m.group(0)):
            col, cn = row.get("column"), row.get("cn")
            if not (col and cn):
                continue
            for c in dims:
                if c["column"] == col:
                    c["label_cn"] = str(cn)[:12]
    except Exception as e:
        print("[warn] LLM 补标签失败（已忽略，不影响候选）：%s" % e, file=sys.stderr)


def apply_approved(schema_path, cands):
    """把 approved=true 的候选写回 schema。幂等：dimensions 按 entity+column 去重合并。"""
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    dims = schema.get("dimensions") or []
    have = {(d.get("entity"), d.get("column")) for d in dims}
    added = 0
    for c in cands:
        if not c.get("approved") or c["kind"] != "dimension":
            continue
        k = (c["entity"], c["column"])
        if k in have:
            continue
        dims.append({"entity": c["entity"], "column": c["column"],
                     "label": c.get("label_cn") or c.get("label"),
                     "values": c.get("values") or [],
                     "rule": "R4-类型维度", "evidence": c.get("evidence", "")})
        have.add(k)
        added += 1
    if not added:
        print("没有 approved=true 的 dimension 候选，schema 未改动。")
        return 0
    # 具名子类走既有落库函数，保持单一实现
    subs = [dict(c["suggestion"], kind="subclass", column=c.get("column"),
                 value=c.get("value"), label=c.get("label"))
            for c in cands if c.get("approved") and c["kind"] == "named_subclass" and c.get("suggestion")]
    schema["dimensions"] = dims
    if subs:
        import schema_ontology as so
        schema = so.apply_named_subclasses(schema, subs)
    backup = schema_path + ".bak"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8", newline="\n") as f:
            json.dump(json.load(open(schema_path, encoding="utf-8")), f, ensure_ascii=False, indent=2)
    with open(schema_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("已写入 %d 个类型维度、%d 个具名子类 → %s（原文件备份 %s）"
          % (added, len(subs), schema_path, backup))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb")
    ap.add_argument("--schema")
    ap.add_argument("--data")
    ap.add_argument("--out", default=None)
    ap.add_argument("--llm", nargs="?", const="auto", default=None,
                    help="补中文标签：local / cloud / auto（可省略值）")
    ap.add_argument("--apply", default=None, help="把该候选文件里 approved=true 的写回 schema")
    a = ap.parse_args()

    if a.apply:
        kb = a.kb or os.path.basename(a.apply).replace("type_suggestions_", "").replace(".json", "")
        sp = a.schema or os.path.join(CODES, "config", "ontology_schema_%s.json" % kb)
        with open(a.apply, encoding="utf-8") as f:
            cands = json.load(f).get("candidates") or []
        return apply_approved(sp, cands)

    if a.schema and a.data:
        import schema_ontology as so
        schema = so.load_schema(a.schema)
        data = so.load_all(a.data)
        kb = os.path.basename(a.data).replace("data_", "")
    elif a.kb:
        schema, data = _load(a.kb)
        kb = a.kb
    else:
        raise SystemExit("需要 --kb 或 --schema/--data")

    cands = suggest(schema, data, a.llm, kb=kb)
    by_kind = {}
    for c in cands:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    doc = {"kb": kb, "version": "1.0",
           "note": "候选仅供参考。审阅后把要用的 approved 改成 true，再 --apply 落库。",
           "counts": by_kind, "candidates": cands}
    # 生成物落到 output/（与 nt 同处），不往 scripts/ 里堆临时文件
    outdir = os.path.join(ROOT, "codes", "output")
    os.makedirs(outdir, exist_ok=True)
    out = a.out or os.path.join(outdir, "type_suggestions_%s.json" % kb)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("候选 %d 条 %s → %s" % (len(cands), by_kind, out))
    for c in cands:
        if c["kind"] == "dimension":
            print("  [%s] %s.%s → %s（%d 个值）"
                  % (c["confidence"], c["entity"], c["column"],
                     c.get("label_cn") or c.get("label"), len(c["values"])))
        elif c["kind"] == "named_subclass":
            print("  [%s] %s ⇒ 子类 %s（%s）" % (c["confidence"], c["entity"], c["label"], c.get("value")))
        else:
            print("  [%s] 层次提示 %s: %s" % (c["confidence"], c["label"], "、".join(c["entities"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
