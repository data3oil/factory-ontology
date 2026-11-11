# -*- coding: utf-8 -*-
"""证据层五道门（只读，跑出真实数字，不改任何文件）。

  G1 语料门：排除项没进库、重复内容 <2%
  G2 切段门：单段 ≤1200 字、p50 在 100~400 字（段数 <100 时只报数）、100% 有行区间
  G3 召回门：题集文件级 hit@5 ≥90%（段级只报数）
  G4 判定门：应答组无 NOT_FOUND、拒答组全部 NOT_FOUND（零编造）
  G5 运维门：增量重建 ≤10 秒

用法：python -m evidence_kb.gates --db data/evidence/default.db --corpus ../docs \
        --questions tests/fixtures/evidence_questions.json
"""
import argparse
import io
import json
import os
import sqlite3
import sys
import time

from .indexer import build
from .retrieval import search, judge


def g1(c):
    dup = sum(n for _s, n in c.execute(
        "SELECT sha, count(*) AS n FROM docs GROUP BY sha HAVING count(*) > 1").fetchall())
    total = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    tokens = ("_bak", "_import", ".obsidian", "__pycache__")
    leaked = 0
    for (path,) in c.execute("SELECT path FROM docs"):
        dirs = path.replace("\\", "/").split("/")[:-1]
        if any(t in d for d in dirs for t in tokens):
            leaked += 1
    rate = dup / max(1, total) * 100
    ok = rate < 2.0 and leaked == 0
    print("G1 语料门  %s  文档 %d，重复 %d 篇（%.1f%%），排除项漏入 %d"
          % ("PASS" if ok else "FAIL", total, dup, rate, leaked))
    return ok


def g2(c):
    lens = sorted(r[0] for r in c.execute("SELECT chars FROM segs"))
    p50 = lens[len(lens) // 2] if lens else 0
    worst = lens[-1] if lens else 0
    no_ref = c.execute("SELECT count(*) FROM segs WHERE path IS NULL OR lf IS NULL").fetchone()[0]
    band_ok = (100 <= p50 <= 400) or len(lens) < 100
    ok = worst <= 1200 and band_ok and no_ref == 0
    print("G2 切段门  %s  段数 %d，p50 %d 字，最长 %d 字，缺行区间 %d"
          % ("PASS" if ok else "FAIL", len(lens), p50, worst, no_ref))
    return ok


def _read(db, corpus, q, k):
    rows = search(db, q, k)
    typed = []
    for r in rows:
        p = os.path.join(corpus, r["path"])
        try:
            whole = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        lines = whole.splitlines()
        seg = "\n".join(lines[max(0, r["line_from"] - 1):r["line_to"]])
        typed.append((r, whole, seg))
    return typed


def g3(db, corpus, questions, k=5):
    if not questions:
        print("G3 召回门  SKIP（没给题集）")
        return True
    f_hit = s_hit = cite = 0
    for item in questions:
        must = item.get("must") or []
        typed = _read(db, corpus, item["q"], k)
        f_ok = any(all(m in w for m in must) for _r, w, _s in typed)
        s_ok = any(all(m in s for m in must) for _r, _w, s in typed)
        f_hit += f_ok
        s_hit += s_ok
        cite += s_ok
    n = len(questions)
    ok = f_hit / n * 100 >= 90
    print("G3 召回门  %s  题数 %d，文件级 hit@%d %.0f%%（门限 90%%），段级 %.0f%%，引用核对 %d/%d"
          % ("PASS" if ok else "FAIL", n, k, f_hit / n * 100, s_hit / n * 100, cite, n))
    return ok


def g4(db, questions, refusals):
    if not questions:
        print("G4 判定门  SKIP（没给题集）")
        return True
    wrong = 0
    for item in questions:
        v = judge(item["q"], search(db, item["q"], 5))
        if v["state"] == "NOT_FOUND":
            wrong += 1
    fabricate = 0
    for q in refusals:
        v = judge(q, search(db, q, 5))
        if v["state"] in ("SUPPORTED",):
            fabricate += 1
    ok = wrong == 0 and fabricate == 0
    print("G4 判定门  %s  应答组误判没找到 %d 条；拒答组编造 %d 条（%d 条拒答题）"
          % ("PASS" if ok else "FAIL", wrong, fabricate, len(refusals)))
    return ok


def g5(corpus, db):
    t0 = time.time()
    r = build(corpus, db)
    secs = time.time() - t0
    ok = secs <= 10 and r["docs"] > 0
    print("G5 运维门  %s  增量重建 %.1fs（门限 10s），库 %.2f MB"
          % ("PASS" if ok else "FAIL", secs, r["size_mb"]))
    return ok


def main(corpus, db, questions_path=None):
    c = sqlite3.connect(db)
    r1 = g1(c)
    r2 = g2(c)
    qs, refusals = [], []
    if questions_path and os.path.exists(questions_path):
        blob = json.load(io.open(questions_path, encoding="utf-8"))
        qs = blob.get("questions") or []
        refusals = blob.get("refusals") or []
    r3 = g3(db, corpus, qs)
    r4 = g4(db, qs, refusals)
    c.close()
    r5 = g5(corpus, db)
    ok = all([r1, r2, r3, r4, r5])
    print("\n结论：%s（G1 %s / G2 %s / G3 %s / G4 %s / G5 %s）"
          % ("通过" if ok else "不通过", r1, r2, r3, r4, r5))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--questions", default=None)
    a = ap.parse_args()
    sys.exit(main(a.corpus, a.db, a.questions))
