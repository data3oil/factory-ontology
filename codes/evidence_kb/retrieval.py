# -*- coding: utf-8 -*-
"""证据层检索与判定：给原文出处，并给出四态结论。

口径来自实测结论（别随手改）：
  1) bm25 召回后按 IDF 覆盖率软加权（硬闸更差，实测 hit@10 从 82% 掉到 59%）；
  2) 稀有词（文档频次 ≤ max(5, N/1000)）命中的段强制进候选并前移；
  3) 同文件、行区间相近（间隔 4 行内、合并后 ≤40 行）的相邻段合并成一条，
     证据跨小节时才不会各缺一半；
  4) 判定四态：SUPPORTED / WEAK / NOT_FOUND / NOT_JUDGED，判定失败绝不阻塞检索。
"""
import datetime
import math
import os
import re
import sqlite3

CJK = "\u4e00-\u9fff"
CJK_SPLIT = re.compile("([%s]+)" % CJK)
CJK_RUN = re.compile("[%s]+" % CJK)
WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
STOP_CH = set("的了是我你他她在有和就不人都一上也很到说要去会着没看好自己这那么些什么可以还把这那个与及或而且但然后")
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|小时|天|台|件|次|元|万元|kWh|度|吨|人|ms|秒)")
STALE_DAYS = int(os.environ.get("EVIDENCE_STALE_DAYS", "730"))


def terms_of(q):
    out, seen = [], set()
    for seg in CJK_SPLIT.split(q or ""):
        if not seg:
            continue
        if CJK_RUN.fullmatch(seg):
            toks = [seg] if len(seg) == 1 else [seg[i:i + 2] for i in range(len(seg) - 1)]
        else:
            toks = [w.lower() for w in WORD_RE.findall(seg)]
        for t in toks:
            if t in seen:
                continue
            if len(t) == 2 and CJK_RUN.fullmatch(t) and (t[0] in STOP_CH or t[1] in STOP_CH):
                continue
            seen.add(t)
            out.append(t)
    return out


def _snippet(body, ts, width=110):
    best = -1
    for t in ts:
        i = body.find(t)
        if i >= 0 and (best < 0 or i < best):
            best = i
    if best < 0:
        return body[:width].replace("\n", " ")
    a = max(0, best - width // 3)
    return body[a:a + width].replace("\n", " ")


def search(db, q, k=8, merge=True, merge_gap=4, merge_max_lines=40):
    ts = terms_of(q)
    if not ts or not os.path.exists(db):
        return []
    c = sqlite3.connect(db)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts5vocab_row USING fts5vocab('segs_fts','row')")
    N = c.execute("SELECT count(*) FROM segs").fetchone()[0] or 1
    idfs, dfs = {}, {}
    for t in ts:
        r = c.execute("SELECT doc FROM fts5vocab_row WHERE term=?", (t,)).fetchone()
        df = r[0] if r else 0
        dfs[t] = df
        idfs[t] = math.log(1 + (N - df + 0.5) / (df + 0.5)) if df else 0.0
    total = sum(idfs.values()) or 1.0
    rare_cut = max(5, N // 1000)
    expr = " AND ".join('"%s"' % t for t in ts[:4]) if len(ts) <= 2 else \
           " OR ".join('"%s"' % t for t in ts)
    rows = c.execute(
        "SELECT s.id, s.path, s.lf, s.lt, s.heading, s.body, s.chars, bm25(segs_fts) AS sc,"
        " d.doc_date FROM segs_fts JOIN segs s ON s.id=segs_fts.rowid"
        " LEFT JOIN docs d ON d.id=s.doc_id WHERE segs_fts MATCH ? ORDER BY sc LIMIT ?",
        (expr, max(60, k * 15))).fetchall()
    seen_ids = {r[0] for r in rows}
    for t in ts:
        rare = dfs.get(t, 0) <= rare_cut
        lim = "" if rare else " LIMIT 80"
        for (rid,) in c.execute("SELECT rowid FROM segs_fts WHERE segs_fts MATCH ?" + lim,
                                ('"%s"' % t,)):
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            got = c.execute("SELECT s.id,s.path,s.lf,s.lt,s.heading,s.body,s.chars,0.0,"
                            "d.doc_date FROM segs s LEFT JOIN docs d ON d.id=s.doc_id"
                            " WHERE s.id=?", (rid,)).fetchone()
            if got:
                rows.append(got)
    c.close()
    cands = []
    for sid, path, lf, lt, head, body, chars, sc, ddate in rows:
        hay = (head or "") + "\n" + body
        cov = sum(idfs.get(t, 0) for t in ts if t in hay) / total
        score = (-sc) * (1 + 1.2 * cov)
        if chars and chars < 80:
            score *= 0.7
        cands.append({"id": sid, "path": path, "lf": lf, "lt": lt, "heading": head or "",
                      "body": body, "score": score, "doc_date": ddate})
    cands.sort(key=lambda x: -x["score"])
    if merge:
        merged = []
        for x in cands:
            placed = False
            for m in merged:
                if (m["path"] == x["path"] and x["lf"] <= m["lt"] + merge_gap
                        and x["lt"] >= m["lf"] - merge_gap
                        and max(m["lt"], x["lt"]) - min(m["lf"], x["lf"]) <= merge_max_lines):
                    m["lf"] = min(m["lf"], x["lf"])
                    m["lt"] = max(m["lt"], x["lt"])
                    m["body"] = (m["body"] + "\n" + x["body"])[:4000]
                    placed = True
                    break
            if not placed:
                merged.append(dict(x))
        merged.sort(key=lambda y: -y["score"])
        cands = merged
    out = []
    for x in cands[:k]:
        out.append({"path": x["path"], "line_from": x["lf"], "line_to": x["lt"],
                    "heading": x["heading"], "snippet": _snippet(x["body"], ts),
                    "score": round(x["score"], 2), "doc_date": x["doc_date"]})
    return out


def judge(q, results, min_cov=0.45, weak_cov=0.25):
    """四态判定：失败只标未判，不抛异常、不阻塞检索。"""
    try:
        if not results:
            return {"state": "NOT_FOUND", "why": "检索没有返回任何候选段", "evidence": []}
        ts = terms_of(q)
        if not ts:
            return {"state": "NOT_JUDGED", "why": "题面切不出有效词", "evidence": []}
        best = max(results, key=lambda r: sum(1 for t in ts if t in (r.get("snippet") or "")))
        cov = sum(1 for t in ts if t in (best.get("snippet") or "")) / len(ts)
        reasons = ["最佳段词覆盖 %.2f（%s:%s）" % (cov, best["path"], best["line_from"])]
        nums = NUM_RE.findall(best.get("snippet") or "")
        by_unit = {}
        for v, u in nums:
            by_unit.setdefault(u, set()).add(v)
        for u, vals in by_unit.items():
            if len(vals) > 1:
                reasons.append("同段出现 %s 的多个数值 %s，口径可能冲突" % (u, "/".join(sorted(vals))))
                return {"state": "WEAK", "why": "；".join(reasons),
                        "evidence": [{"path": best["path"], "line_from": best["line_from"],
                                      "line_to": best["line_to"]}]}
        d0 = best.get("doc_date")
        if d0:
            try:
                age = (datetime.date.today() - datetime.date.fromisoformat(str(d0)[:10])).days
                if age > STALE_DAYS:
                    reasons.append("依据文档日期 %s，已 %d 天（阈值 %d）" % (d0, age, STALE_DAYS))
                    return {"state": "WEAK", "why": "；".join(reasons),
                            "evidence": [{"path": best["path"], "line_from": best["line_from"],
                                          "line_to": best["line_to"]}]}
            except Exception:
                pass
        state = "SUPPORTED" if cov >= min_cov else ("WEAK" if cov >= weak_cov else "NOT_FOUND")
        if state == "NOT_FOUND":
            reasons.append("低于弱依据线 %.2f" % weak_cov)
        return {"state": state, "why": "；".join(reasons),
                "evidence": [{"path": best["path"], "line_from": best["line_from"],
                              "line_to": best["line_to"]}]}
    except Exception as e:                                   # noqa: BLE001
        return {"state": "NOT_JUDGED", "why": "判定层异常：%s" % repr(e)[:80], "evidence": []}


def status(db):
    if not os.path.exists(db):
        return {"ok": True, "built": False, "db": db, "docs": 0, "segs": 0}
    c = sqlite3.connect(db)
    d, s = c.execute("SELECT (SELECT count(*) FROM docs),(SELECT count(*) FROM segs)").fetchone()
    dates = c.execute("SELECT min(doc_date), max(doc_date) FROM docs").fetchone()
    c.close()
    return {"ok": True, "built": True, "db": db, "docs": d, "segs": s,
            "date_min": dates[0], "date_max": dates[1],
            "size_mb": round(os.path.getsize(db) / 1e6, 2)}
