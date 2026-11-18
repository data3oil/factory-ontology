#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_cq_baseline.py — 按库「能力问题（CQ）」验收门（纯标准库，不起 HTTP）。

为什么要有它
------------
`eval_qa.py` 的 331 题是**从 CSV 自动生成**的，验的是"数据在不在、引擎会不会算"。
它验不了一件事：**业务上真会问的问题，本体建得够不够支撑**。

CQ（Competency Questions）是本体工程的标准验收手段：先由懂业务的人写下"这套本体
必须能回答的问题"，再拿建成的本体逐题考。本门就是这件事的固化：

  · 问题人工写（业务语言，不是模板生成）；
  · golden 答案由 CSV 直接统计得出，写进 `source` 字段，**不看引擎输出**；
    —— 这点很关键。先看引擎答什么再写 golden，等于把 bug 固化成基线。
  · 每次改建本/问答都跑一遍，hit 不得低于基线，且**不得出现编造**。

与 eval_qa 的分工：eval_qa 管"量"（331 题覆盖率），本门管"质"（业务问题答不答得上）。

判定口径
--------
  ok         答案满足期望（含全部/任一关键词，或对无答案题正确拒答）
  miss       引擎诚实拒答，但本该答得出（能力缺口，不算错，但计分）
  wrong      给了具体答案但与 golden 不符（答错）
  fabricate  无答案题却断言了具体答案；或对不存在的字段给出"0/无"以外的具体值
                            —— 这是硬失败，编造比答不出严重得多

用法
----
  python scripts/verify_cq_baseline.py                 # 跑全部库
  python scripts/verify_cq_baseline.py --kb valve      # 单库
  python scripts/verify_cq_baseline.py --verbose       # 打印每题答案
  python scripts/verify_cq_baseline.py --update-baseline   # 把当前 hit 写回基线（人工确认后再用）
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
CQDIR = os.path.join(SCRIPTS, "cq")
for p in (SCRIPTS, os.path.join(ROOT, "codes")):
    if p not in sys.path:
        sys.path.insert(0, p)


def judge_cq(q, ans):
    """返回 (bucket, detail)。bucket ∈ ok/miss/wrong/fabricate。"""
    exp = q.get("expect") or {}
    mode = exp.get("mode", "all")
    vals = [str(v) for v in (exp.get("values") or [])]
    refused = _is_refuse(ans)
    if mode == "refuse":
        # 无答案题：只有"明确拒答"才算过。答"0/无"是把缺失当零，仍属断言不存在的事实。
        return ("ok", "拒答") if refused else ("fabricate", "编造：" + ans[:60])
    if refused:
        return "miss", "拒答"
    if mode == "all":
        return ("ok", "") if all(v in ans for v in vals) else ("wrong", "缺 " + str([v for v in vals if v not in ans]))
    if mode == "any":
        return ("ok", "") if any(v in ans for v in vals) else ("wrong", "均未命中")
    if mode == "none":
        return ("ok", "") if not any(v in ans for v in vals) else ("wrong", "不该出现 " + str(vals))
    return "wrong", "未知判定模式 " + mode


def _is_refuse(ans):
    """复用 eval_qa 的拒答判定，保证与主评测口径一致。"""
    import eval_qa as EQ
    return bool(EQ._is_refuse(ans))


def run_one(path, verbose=False):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    kb = spec["kb"]
    import eval_qa as EQ
    ask = EQ.get_engine(kb)
    rows, counts = [], {"ok": 0, "miss": 0, "wrong": 0, "fabricate": 0}
    for q in spec.get("questions", []):
        ans = ask(q["question"])
        bucket, detail = judge_cq(q, ans)
        counts[bucket] += 1
        rows.append((q["id"], q["question"], bucket, detail, ans if verbose else ""))
    return spec, counts, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=None, help="只跑某个库（缺省=全部）")
    ap.add_argument("--verbose", action="store_true", help="打印每题答案")
    ap.add_argument("--update-baseline", action="store_true", help="把当前 ok 数写回基线文件")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CQDIR, "*.json")))
    if a.kb:
        files = [f for f in files if os.path.basename(f)[:-5] == a.kb]
    if not files:
        print("未找到 CQ 基线文件：%s" % CQDIR)
        return 2

    total = {"ok": 0, "miss": 0, "wrong": 0, "fabricate": 0}
    failed, regressed = [], []
    for path in files:
        spec, counts, rows = run_one(path, a.verbose)
        kb = spec["kb"]
        base = int(spec.get("baseline_hit") or 0)
        n = sum(counts.values())
        print("\n[%s] %d/%d 命中  (miss %d / wrong %d / fabricate %d)  基线 %d"
              % (kb, counts["ok"], n, counts["miss"], counts["wrong"], counts["fabricate"], base))
        for rid, question, bucket, detail, ans in rows:
            mark = {"ok": "OK  ", "miss": "MISS", "wrong": "WRONG", "fabricate": "FAB"}[bucket]
            line = "  %-5s %-6s %s" % (mark, rid, question)
            if detail:
                line += "  ← " + detail
            print(line)
            if ans:
                print("        → " + str(ans).replace("\n", " ")[:160])
        for k in total:
            total[k] += counts[k]
        if counts["fabricate"]:
            failed.append((kb, "编造 %d 题" % counts["fabricate"]))
        if base and counts["ok"] < base:
            regressed.append((kb, "命中 %d < 基线 %d" % (counts["ok"], base)))
        if a.update_baseline:
            spec["baseline_hit"] = counts["ok"]
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(spec, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print("  基线已更新为 %d" % counts["ok"])

    n = sum(total.values())
    print("\n合计 %d 题：ok %d / miss %d / wrong %d / fabricate %d"
          % (n, total["ok"], total["miss"], total["wrong"], total["fabricate"]))
    if a.update_baseline:
        print("基线已写回，本次不判失败。")
        return 0
    if failed or regressed:
        for kb, msg in failed + regressed:
            print("FAIL %s: %s" % (kb, msg))
        return 1
    print("PASS（无编造，命中不低于基线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
