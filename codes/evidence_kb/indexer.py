# -*- coding: utf-8 -*-
"""证据层：把原始文档切成段、建可回引的索引，供"哪份文件第几行"这类问题使用。

与现有 RAG 的分工（只加不改）：
  - 现有 bm25_retrieval / 向量 / RRF 负责"哪个实体、哪类知识相关"；
  - 本模块负责"这段原文在哪，能否逐字核对"，输出 path + 行区间 + 片段。

设计约束：
  - 纯标准库（sqlite3 + re），零外部依赖、零模型、数据不出厂；
  - 独立库文件（默认 codes/data/evidence/kb.db），不动本体与图谱数据；
  - 只读查询默认安全，重建索引由显式 CLI 或 admin 端点触发。
"""
import hashlib
import io
import os
import re
import sqlite3
import time

CJK = "\u4e00-\u9fff"
CJK_SPLIT = re.compile("([%s]+)" % CJK)
CJK_RUN = re.compile("[%s]+" % CJK)
WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SENT_RE = re.compile(r"(?<=[。！？；\n])")
DATE_PATTERNS = [
    r"(20\d{2})-(\d{1,2})-(\d{1,2})",
    r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
    r"(20\d{2})/(\d{1,2})/(\d{1,2})",
]
MAX_SEG = 1200
MIN_SEG = 30


def bigrams(text):
    out = []
    for seg in CJK_SPLIT.split(text):
        if not seg:
            continue
        if CJK_RUN.fullmatch(seg):
            out.extend([seg] if len(seg) == 1 else [seg[i:i + 2] for i in range(len(seg) - 1)])
        else:
            out.extend(w.lower() for w in WORD_RE.findall(seg))
    return " ".join(out)


def split_by_heading(text):
    lines = text.splitlines()
    blocks, cur, start, trail = [], [], 1, {}
    for i, ln in enumerate(lines, 1):
        m = H_RE.match(ln)
        if m and cur:
            blocks.append((start, i - 1, "\n".join(cur), dict(trail)))
            cur, start = [], i
        if m:
            lvl = len(m.group(1))
            trail = {k: v for k, v in trail.items() if k < lvl}
            trail[lvl] = m.group(2).strip()
        cur.append(ln)
    if cur:
        blocks.append((start, len(lines), "\n".join(cur), dict(trail)))
    return blocks


def segment(text, max_seg=MAX_SEG):
    for a, b, body, trail in split_by_heading(text):
        head = " > ".join(trail[k] for k in sorted(trail))
        body = body.strip()
        if not body:
            continue
        if len(body) <= max_seg:
            if len(body) >= MIN_SEG:
                yield a, b, head, body
            continue
        pos = a
        for piece in re.split(r"\n\s*\n", body):
            piece = piece.strip()
            if not piece:
                continue
            if len(piece) <= max_seg:
                if len(piece) >= MIN_SEG:
                    yield pos, pos + piece.count("\n"), head, piece
                pos += piece.count("\n") + 2
                continue
            lines = piece.split("\n")
            if len(lines) > 3:
                buf, at = [], pos
                for ln in lines:
                    if len(ln) > max_seg:
                        if buf:
                            yield at, at + len(buf) - 1, head, "\n".join(buf).strip()
                            at += len(buf)
                            buf = []
                        for i in range(0, len(ln), max_seg):
                            yield at, at, head, ln[i:i + max_seg]
                            at += 1
                        continue
                    cand = "\n".join(buf + [ln])
                    if len(cand) > max_seg and buf:
                        yield at, at + len(buf) - 1, head, "\n".join(buf).strip()
                        at += len(buf)
                        buf = []
                    buf.append(ln)
                if buf and len("\n".join(buf).strip()) >= MIN_SEG:
                    yield at, at + len(buf) - 1, head, "\n".join(buf).strip()
                pos += piece.count("\n") + 2
                continue
            buf, at = "", pos
            for s in SENT_RE.split(piece):
                if not s:
                    continue
                if len(buf) + len(s) > max_seg and buf:
                    yield at, at + buf.count("\n"), head, buf.strip()
                    at += buf.count("\n")
                    buf = ""
                buf += s
                while len(buf) > max_seg:
                    yield at, at + buf.count("\n"), head, buf[:max_seg]
                    at += buf[:max_seg].count("\n")
                    buf = buf[max_seg:]
            if buf.strip():
                yield at, at + buf.count("\n"), head, buf.strip()
            pos += piece.count("\n") + 2


def doc_date_of(text, mtime):
    best = None
    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, text[:4000]):
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                    best = max(best or "", "%04d-%02d-%02d" % (y, mo, d))
            except Exception:
                continue
    if best:
        return best
    import datetime
    return datetime.date.fromtimestamp(mtime).isoformat()


def schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE,
      mtime REAL, size INT, sha TEXT, doc_date TEXT);
    CREATE TABLE IF NOT EXISTS segs(id INTEGER PRIMARY KEY, doc_id INT, path TEXT,
      lf INT, lt INT, heading TEXT, body TEXT, tok TEXT, chars INT);
    CREATE INDEX IF NOT EXISTS idx_segs_doc ON segs(doc_id);
    CREATE VIRTUAL TABLE IF NOT EXISTS segs_fts USING fts5(
      tok, content='segs', content_rowid='id', tokenize='unicode61');
    """)


def build(corpus, db, full=False, exclude=("__pycache__", ".git", "node_modules")):
    """建库（默认增量：只重建 mtime/size 变了的文档）。返回统计 dict。"""
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    t0 = time.time()
    c = sqlite3.connect(db)
    schema(c)
    if full:
        c.execute("DROP TABLE IF EXISTS segs_fts")
        for t in ("segs", "docs"):
            c.execute("DELETE FROM %s" % t)
        c.execute("CREATE VIRTUAL TABLE segs_fts USING fts5(tok, content='segs',"
                  " content_rowid='id', tokenize='unicode61')")
        c.commit()
    docs = []
    for dp, dn, fn in os.walk(corpus):
        dn[:] = [d for d in dn if not any(x in d for x in exclude) and not d.startswith(".")]
        for f in fn:
            if not f.endswith(".md"):
                continue
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            docs.append((os.path.relpath(p, corpus).replace("\\", "/"), p, st.st_mtime, st.st_size))
    known = {r[0]: (r[1], r[2], r[3]) for r in
             c.execute("SELECT path, id, mtime, size FROM docs")}
    added = updated = skipped = 0
    for path, full_p, mtime, size in sorted(docs):
        old = known.get(path)
        if old and not full and abs(old[1] - mtime) < 1 and old[2] == size:
            skipped += 1
            continue
        text = io.open(full_p, encoding="utf-8", errors="replace").read()
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        ddate = doc_date_of(text, mtime)
        if old:
            doc_id = old[0]
            c.execute("DELETE FROM segs_fts WHERE rowid IN (SELECT id FROM segs WHERE doc_id=?)",
                      (doc_id,))
            c.execute("DELETE FROM segs WHERE doc_id=?", (doc_id,))
            c.execute("UPDATE docs SET mtime=?,size=?,sha=?,doc_date=? WHERE id=?",
                      (mtime, size, sha, ddate, doc_id))
            updated += 1
        else:
            doc_id = c.execute("INSERT INTO docs(path,mtime,size,sha,doc_date)"
                               " VALUES(?,?,?,?,?)", (path, mtime, size, sha, ddate)).lastrowid
            added += 1
        for lf, lt, head, body in segment(text):
            tok = bigrams(body)
            sid = c.execute("INSERT INTO segs(doc_id,path,lf,lt,heading,body,tok,chars)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            (doc_id, path, lf, lt, head, body, tok, len(body))).lastrowid
            c.execute("INSERT INTO segs_fts(rowid,tok) VALUES(?,?)", (sid, tok))
    seen = {p for p, *_ in docs}
    removed = 0
    for path, doc_id in c.execute("SELECT path, id FROM docs").fetchall():
        if path not in seen:
            c.execute("DELETE FROM segs_fts WHERE rowid IN (SELECT id FROM segs WHERE doc_id=?)",
                      (doc_id,))
            c.execute("DELETE FROM segs WHERE doc_id=?", (doc_id,))
            c.execute("DELETE FROM docs WHERE id=?", (doc_id,))
            removed += 1
    c.commit()
    c.execute("INSERT INTO segs_fts(segs_fts) VALUES('optimize')")
    c.commit()
    ndoc, nseg = c.execute("SELECT (SELECT count(*) FROM docs),(SELECT count(*) FROM segs)").fetchone()
    c.close()
    return {"docs": ndoc, "segs": nseg, "added": added, "updated": updated,
            "skipped": skipped, "removed": removed,
            "secs": round(time.time() - t0, 1), "db": db,
            "size_mb": round(os.path.getsize(db) / 1e6, 2)}


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="证据层建库")
    ap.add_argument("--corpus", default=os.environ.get("EVIDENCE_KB_CORPUS", ""))
    ap.add_argument("--db", default=os.environ.get("EVIDENCE_KB_DB", ""))
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    corpus = a.corpus or os.path.join(os.path.dirname(root), "docs")
    db = a.db or os.path.join(root, "data", "evidence", "kb.db")
    print(json.dumps(build(corpus, db, a.full), ensure_ascii=False))
