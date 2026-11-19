#!/usr/bin/env python3
"""bench_graph_store.py — 图存储规模基准（零依赖）

做什么：
    生成 N 个实体（默认 20 万）的合成 N-Triples，对比
    1) 直接解析 .nt 建内存图；2) SQLite 落库后加载。
    给出耗时、峰值内存（tracemalloc）、库文件大小。

口径：
    合成数据只用于压规模，不代表业务分布。数字写进 docs/规模化.md 前必须标明这一点。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "codes"))

NS = "http://bench.example/ontology#"


def gen_nt(path: str, n: int) -> dict:
    t0 = time.time()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for i in range(n):
            ent = "<%sEquipment_E%06d>" % (NS, i)
            f.write("%s <%stype> <%sEquipment> .\n" % (ent, NS, NS))
            f.write('%s <%slabel> "设备%d" .\n' % (ent, NS, i))
            f.write('%s <%sstatus> "%s" .\n' % (ent, NS, "运行" if i % 3 else "停机"))
            f.write('%s <%spowerKw> "%d" .\n' % (ent, NS, i % 50))
            f.write("%s <%slocatedIn> <%sWorkshop_W%02d> .\n" % (ent, NS, NS, i % 20))
    return {"gen_seconds": round(time.time() - t0, 2),
            "mb": round(os.path.getsize(path) / 1048576, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200000)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="graphbench_")
    nt = os.path.join(tmp, "bench.nt")
    db = os.path.join(tmp, "bench.db")
    print("生成 %d 个实体 ..." % args.n)
    meta = gen_nt(nt, args.n)
    print("  文件 %.1f MB，用时 %ss" % (meta["mb"], meta["gen_seconds"]))

    import graph_rag
    import graph_store

    t0 = time.time()
    g = graph_rag.build_graph(nt)
    t_nt = time.time() - t0

    t0 = time.time()
    stats = graph_store.build(nt, db)
    t_build = time.time() - t0

    t0 = time.time()
    g2 = graph_store.load(db)
    t_load = time.time() - t0

    print("解析 .nt 建图: %.2fs，节点 %d" % (t_nt, len(g[0])))
    print("SQLite 落库:    %.2fs，库 %.1f MB，三元组 %d" % (t_build, stats["size_mb"] or 0, stats["triples"]))
    print("SQLite 加载:    %.2fs，节点 %d（%.1fx）"
          % (t_load, len(g2[0]), t_nt / max(t_load, 1e-6)))
    print("\n合成数据仅用于压规模，不代表业务分布。")
    if not args.keep:
        for p in (nt, db):
            if os.path.exists(p):
                os.replace(p, p + ".done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
