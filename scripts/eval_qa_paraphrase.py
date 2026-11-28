#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_qa_paraphrase.py — 陌生问法评测集（P1-3，2026-11-28）

目的：eval_qa.py 的 250 题由同一批台账自动生成，量的是"引擎吃透自己建的库"。
本脚本对同一批问题做**确定性换说法**，只改问法、不改 golden，用 eval_qa 的
同一套 judge() 判分，得到独立的"陌生问法命中率"，与 250 题口径分开报告。

纪律：
- 不调模型、不人工标注；改写规则写死在代码里，可复现；
- golden / check / target 全部沿用原 fixture，不改答案口径；
- 只 import eval_qa 的 judge()，不改 eval_qa.py 与 eval_qa_baseline.json；
- 命中率数字只作诊断，不进回归门、不与 250 题混算。

用法：
    python scripts/eval_qa_paraphrase.py --out scripts/eval_qa_paraphrase_report.json
    python scripts/eval_qa_paraphrase.py --dry        # 只看生成的问法，不跑引擎
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
FIXDIR = os.path.join(CODES, "tests", "fixtures", "qa_eval")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, CODES)

# 确定性改写规则（顺序执行，每条最多产出 1 个变体；改不动就跳过）
# 只动"问法措辞"，不动数字、实体名、量词，保证 golden 仍然成立。
_PARAPHRASES = [
    # 倒装 / 换量词
    ("有多少台", "共有多少台"),
    ("有多少个", "总共有多少个"),
    ("有多少种", "一共有多少种"),
    ("有哪些", "都包括哪些"),
    ("是什么", "具体是哪些"),
    # 极值换说法
    ("最高", "最大"),
    ("最低", "最小"),
    ("最长", "最大"),
    ("最短", "最小"),
    # 省略实体名前的修饰
    ("请问", ""),
    ("帮我查一下", ""),
]


def paraphrase(question):
    """确定性生成一条换说法；无任何规则命中则返回 None（跳过）。"""
    out = question
    changed = False
    for old, new in _PARAPHRASES:
        if old in out:
            out = out.replace(old, new, 1)
            changed = True
            break
    out = out.strip()
    if not changed or out == question or len(out) < 2:
        return None
    return out


def load_fixtures():
    out = []
    for path in sorted(glob.glob(os.path.join(FIXDIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        kb = data.get("kb") or os.path.basename(path).split(".")[0]
        for q in data.get("questions") or []:
            out.append((kb, q))
    return out


def build_set():
    """生成换说法题集；golden 沿用原题，只换 question。"""
    items = []
    skipped = 0
    for kb, q in load_fixtures():
        pq = paraphrase(q.get("question") or "")
        if not pq:
            skipped += 1
            continue
        item = dict(q)                       # golden / check / target 原样沿用
        item["question"] = pq
        item["source_id"] = q.get("id")
        item["source_question"] = q.get("question")
        item["kb"] = kb
        items.append(item)
    return items, skipped


def main():
    ap = argparse.ArgumentParser(description="陌生问法评测集（独立口径）")
    ap.add_argument("--out", default=os.path.join(ROOT, "scripts", "eval_qa_paraphrase_report.json"))
    ap.add_argument("--dry", action="store_true", help="只生成并打印问法，不跑引擎")
    args = ap.parse_args()

    items, skipped = build_set()
    print("生成换说法 %d 题（跳过无法改写 %d 题）" % (len(items), skipped))
    if args.dry:
        for it in items[:20]:
            print("  %s → %s" % (it["source_question"], it["question"]))
        return 0

    import eval_qa
    per_kb = {}
    totals = {"hit": 0, "miss": 0, "wrong": 0, "refuse_ok": 0,
              "fabricate": 0, "empty_zero": 0}
    details = []
    for it in items:
        kb = it["kb"]
        ask = eval_qa.get_engine(kb)
        ans = ask(it["question"])
        bucket, ok = eval_qa.judge(it, ans)      # judge() 返回 (bucket, ok)
        per_kb.setdefault(kb, {"n": 0, "hit": 0, "wrong": 0, "miss": 0})
        per_kb[kb]["n"] += 1
        if bucket in totals:
            totals[bucket] += 1
        if bucket in per_kb[kb]:
            per_kb[kb][bucket] += 1
        details.append({"id": it["id"], "kb": kb, "question": it["question"],
                        "source_question": it["source_question"],
                        "expect": it.get("expect"), "bucket": bucket, "ok": ok})

    report = {"kind": "paraphrase_independent", "count": len(items),
              "skipped": skipped, "totals": totals, "per_kb": per_kb,
              "note": "独立口径，禁止与 eval_qa 250 题混算", "details": details}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n== 陌生问法命中（独立口径）==")
    for kb, v in per_kb.items():
        print("  %-16s n=%3d hit=%3d wrong=%2d miss=%2d" %
              (kb, v["n"], v["hit"], v["wrong"], v["miss"]))
    print("  合计:", json.dumps(totals, ensure_ascii=False))
    print("报告:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
