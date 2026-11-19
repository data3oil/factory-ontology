#!/usr/bin/env python3
"""graph_store.py — 图持久化（SQLite，零依赖）

为什么需要它：
    内存图每次启动都要解析 .nt 重建，10 万实体以上启动时间不可接受，
    且没有跨重启复用。这个模块把三元组落到 SQLite，加载时只读表，
    返回与 graph_rag.build_graph 完全同构的四元组，调用方零改动。

边界：
    - 只做持久化与加载，不做查询优化（查询仍在内存图里跑）。
    - 100 万以上仍建议换图数据库，这里不假装能扛。
    - 零第三方依赖，只用标准库 sqlite3。

用法：
    python graph_store.py build <nt> <db>
    python graph_store.py stats <db>
    python graph_store.py load  <db>     # 打印节点/边数，验证可加载
"""
from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict

TABLES = """
CREATE TABLE IF NOT EXISTS triple (s TEXT NOT NULL, p TEXT NOT NULL,
                                   o TEXT NOT NULL, is_entity INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS node (iri TEXT PRIMARY KEY, label TEXT);
CREATE TABLE IF NOT EXISTS val_index (val TEXT NOT NULL, iri TEXT NOT NULL);
"""
# 索引放到插入之后建：先建索引再插入，5 万实体的落库时间从 29s 降到几秒。
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_triple_s ON triple(s);
CREATE INDEX IF NOT EXISTS ix_triple_o ON triple(o);
CREATE INDEX IF NOT EXISTS ix_val ON val_index(val);
"""


def tail(uri: str) -> str:
    return uri.split("#")[-1] if "#" in uri else uri.split("/")[-1]


def _parse_nt(nt_file: str):
    """优先复用主解析器；它若不可用则用最小解析（保证本模块可独立跑）。"""
    try:
        from ontology_qa_v3 import parse_nt as _p
        return _p(nt_file)
    except Exception:
        out = []
        with open(nt_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.rstrip(" .").split(" ", 2)
                if len(parts) != 3:
                    continue
                s, p, o = parts
                out.append((s.strip("<>"), p.strip("<>"), o.strip("<>").strip('"')))
        return out


def build(nt_file: str, db_path: str) -> dict:
    """把 .nt 落成 SQLite。重复执行会先清表再写，幂等。"""
    if os.path.exists(db_path):
        os.replace(db_path, db_path + ".bak")
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(TABLES)
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA journal_mode=MEMORY")
        rows = []
        labels = {}
        for s, p, o in _parse_nt(nt_file):
            pn = tail(p)
            is_ent = 1 if str(o).startswith(("http://", "https://")) else 0
            rows.append((s, pn, o, is_ent))
            if pn == "label":
                labels[s] = str(o).strip('"')
            elif pn == "type" and tail(o) == "Class":
                labels[s] = tail(s)
        conn.executemany("INSERT INTO triple VALUES (?,?,?,?)", rows)
        conn.executemany("INSERT OR REPLACE INTO node VALUES (?,?)", list(labels.items()))
        # 值索引：字面值 + 局部名 + 实例 ID
        vi = []
        for s in {r[0] for r in rows}:
            vi.append((tail(s).lower(), s))
            parts = tail(s).split("_")
            if len(parts) >= 2:
                vi.append((parts[-1].lower(), s))
        for s, p, o, is_ent in rows:
            if not is_ent and len(str(o)) <= 40:
                vi.append((str(o).lower(), s))
        conn.executemany("INSERT INTO val_index VALUES (?,?)", vi)
        conn.executescript(INDEXES)
        conn.commit()
        st = stats(conn)
        st["size_mb"] = round(os.path.getsize(db_path) / 1048576, 1)
        return st
    finally:
        conn.close()


def stats(conn_or_path) -> dict:
    own = isinstance(conn_or_path, str)
    conn = sqlite3.connect(conn_or_path) if own else conn_or_path
    try:
        t = conn.execute("SELECT count(*) FROM triple").fetchone()[0]
        n = conn.execute("SELECT count(DISTINCT s) FROM triple").fetchone()[0]
        e = conn.execute("SELECT count(*) FROM triple WHERE is_entity=1").fetchone()[0]
        return {"triples": t, "nodes": n, "entity_edges": e,
                "size_mb": round(os.path.getsize(conn_or_path) / 1048576, 1) if own else None}
    finally:
        if own:
            conn.close()


def load(db_path: str):
    """返回 (graph, labels, value_index, reverse)，与 graph_rag.build_graph 同构。"""
    conn = sqlite3.connect(db_path)
    try:
        graph = defaultdict(lambda: defaultdict(list))
        reverse = defaultdict(lambda: defaultdict(list))
        labels = {}
        value_index = defaultdict(list)
        for s, p, o, is_ent in conn.execute(
                "SELECT s,p,o,is_entity FROM triple ORDER BY s"):
            if p == "type":
                if tail(o) == "Class":
                    labels[s] = tail(s)
                continue
            if p == "label":
                labels[s] = str(o).strip('"')
                continue
            if is_ent:
                graph[s][p].append(o)
                reverse[o]["~" + p].append(s)
            else:
                graph[s][p].append(o)
                if len(str(o)) <= 40:
                    value_index[str(o).lower()].append(s)
        for iri, label in conn.execute("SELECT iri,label FROM node"):
            labels[iri] = label
        for val, iri in conn.execute("SELECT val,iri FROM val_index"):
            value_index[val].append(iri)
        # 补齐未在 node 表出现的实体
        for ent in graph:
            if ent not in labels:
                labels[ent] = tail(ent)
        return graph, labels, value_index, reverse
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "build":
        print(build(sys.argv[2], sys.argv[3]))
    elif cmd == "stats":
        print(stats(sys.argv[2]))
    elif cmd == "load":
        g, l, v, r = load(sys.argv[2])
        print({"nodes": len(g), "labels": len(l), "values": len(v), "reverse": len(r)})
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
