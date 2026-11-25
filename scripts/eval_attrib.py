#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_attrib.py — 跨库命中率 + 未命中归因（in-process，不走 HTTP，无需 key）。

为什么另起一个：`eval_hit_rate.py` 走 HTTP，需要起服务 + 钥匙；评测本身不该依赖部署。
这里复用它的**同一套题生成器与同一套判分**（import gen_questions / judge / classify_stage），
只把取答换成 in-process 调 `ontology_qa_v3.answer`，口径不变。

用法：
  python scripts/eval_attrib.py --kb valve --kb chem --kb food --kb auto_parts --n 12
  python scripts/eval_attrib.py --all --n 12        # 跑 data_* 下全部有 nt 的库

输出：scripts/eval_attrib_<kb>.json + 终端汇总表（命中率 / 分题型 / 归因分布）
"""
import argparse
import glob
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, CODES)
sys.path.insert(0, SCRIPTS)

import eval_hit_rate as EH  # noqa: E402
from eval_hit_rate import classify_stage, gen_questions, judge, load_lexicon, load_tables  # noqa: E402

import ontology_qa_v3 as qa  # noqa: E402


def _is_assoc(rows):
    """与 eval_hit_rate.main 同一判据：两个以上外键、且几乎没有自身属性 → N 元关系表。"""
    cols = list(rows[0].keys())
    fks = [c for c in cols if c.endswith("_id") and c != "id"]
    others = [c for c in cols if c not in fks and c != "id"]
    return len(fks) >= 2 and len(others) <= 1


def run_kb(kb, n_per_type=12):
    data_dir = os.path.join(CODES, "data_%s" % kb)
    nt = os.path.join(CODES, "output", "%s.nt" % kb)
    lex = os.path.join(CODES, "config", "lexicon_%s.json" % kb)
    for p in (data_dir, nt, lex):
        if not os.path.exists(p):
            return {"kb": kb, "skip": "缺 %s" % os.path.basename(p)}
    tables = {k: v for k, v in load_tables(data_dir).items() if v and not _is_assoc(v)}
    # 关键：把词典的中英映射灌进 gen_questions 的模块级 LEX_ENT/LEX_ATTR，
    # 否则题面会退化成英文表名（"batches有多少条记录"），等于换了另一套更难的题。
    # eval_hit_rate.main 里也是先 load_lexicon 再 gen_questions，这里必须同序。
    EH.LEX_ENT, EH.LEX_ATTR = load_lexicon(kb)
    qs = gen_questions(tables, n_per_type=n_per_type)
    D = qa.load_dict(lex)
    data = qa.build_data(qa.parse_nt(nt), D)

    hit, miss, by_type, times = 0, [], {}, []
    for q in qs:
        t0 = time.perf_counter()
        try:
            ans = str(qa.answer(q["q"], data, D))
        except Exception as e:
            ans = "[异常] %s" % e
        times.append(time.perf_counter() - t0)
        ok = judge(q, ans)
        d = by_type.setdefault(q["type"], {"hit": 0, "n": 0})
        d["n"] += 1
        d["hit"] += 1 if ok else 0
        if ok:
            hit += 1
        else:
            miss.append({"q": q["q"], "type": q["type"], "expect": q.get("golden"),
                         "got": ans[:150],
                         "stage": classify_stage(q, ans, D.get("entity_cn2en") or {},
                                                 D.get("attr_en2cn") or {})})
    rate = hit / len(qs) if qs else 0.0
    dist = {}
    for m in miss:
        dist[m["stage"]] = dist.get(m["stage"], 0) + 1
    res = {"kb": kb, "n": len(qs), "hit": hit, "rate": round(rate, 4),
           "by_type": by_type, "stage_dist": dist, "miss": miss,
           "p50_ms": round(statistics.median(times) * 1000, 1) if times else None,
           "engine": "ontology_qa_v3.answer (in-process)",
           "question_source": "eval_hit_rate.gen_questions（CSV 原始表名/列名口径）",
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    out = os.path.join(SCRIPTS, "eval_attrib_%s.json" % kb)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--n", type=int, default=12)
    a = ap.parse_args()

    kbs = list(a.kb)
    if a.all:
        for p in sorted(glob.glob(os.path.join(CODES, "output", "*.nt"))):
            kbs.append(os.path.splitext(os.path.basename(p))[0])
    if not kbs:
        print("用法: --kb valve --kb chem 或 --all")
        return 2

    rows = []
    for kb in kbs:
        r = run_kb(kb, a.n)
        if r.get("skip"):
            print("跳过 %-14s %s" % (kb, r["skip"]))
            continue
        rows.append(r)
        print("%-14s %2d/%2d = %5.1f%%  p50=%sms  归因=%s" % (
            kb, r["hit"], r["n"], r["rate"] * 100, r["p50_ms"], r["stage_dist"] or "{}"))

    if rows:
        tot_n = sum(r["n"] for r in rows)
        tot_h = sum(r["hit"] for r in rows)
        print("\n合计 %d/%d = %.1f%%（%d 个库）" % (tot_h, tot_n, 100.0 * tot_h / tot_n, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
