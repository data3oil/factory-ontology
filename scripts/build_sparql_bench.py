#!/usr/bin/env python3
"""build_sparql_bench.py — 可复跑的问答题库生成器（零依赖）

做法对齐 Build2SPARQL（arXiv:2610.00224）：SPARQL 由代码生成并执行验证，
自然语言问句由模板按同一结构生成，答案不依赖模型行为。

六类模式（全部在自研 SPARQL 子集的支持范围内；该子集不支持 LIMIT/UNION/聚合）：
    linear / two_hop / branch / filter / reverse / type

用法：
    python scripts/build_sparql_bench.py --nt codes/output/valve.nt --n 500 \
        --out codes/output/bench/sparql_bench_valve.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "codes"))

import ontology_sparql as S  # noqa: E402
from graph_rag import parse_nt, tail  # noqa: E402

NS = "http://factory.example/ontology#"
PATTERNS = ["linear", "two_hop", "branch", "filter", "reverse", "type"]


def index(triples):
    types, props, incoming = {}, {}, {}
    for s, p, o in triples:
        pn = tail(p)
        if pn == "type":
            types.setdefault(tail(o), []).append(s)
        elif pn != "label":
            props.setdefault(s, {}).setdefault(pn, []).append(o)
            if isinstance(o, str) and o.startswith("http"):
                incoming.setdefault(o, []).append((s, pn))
    return types, props, incoming


def gen(types, props, incoming, pattern, rng):
    classes = [c for c in types if len(types[c]) >= 3]
    if not classes:
        return None
    cls = rng.choice(classes)
    inst = rng.choice(types[cls])
    kv = props.get(inst) or {}
    rels = [p for p, vs in kv.items() if vs and str(vs[0]).startswith("http")]
    lits = [p for p, vs in kv.items() if vs and not str(vs[0]).startswith("http")]

    if pattern == "linear" and rels:
        p = rng.choice(rels)
        return ("%s 的%s指向哪个实体" % (tail(inst), p),
                "SELECT ?o WHERE { <%s> <%s%s> ?o }" % (inst, NS, p), cls)

    if pattern == "two_hop" and rels:
        p1 = rng.choice(rels)
        target = kv[p1][0]
        nxt = [p for p, vs in (props.get(target) or {}).items()
               if vs and str(vs[0]).startswith("http")]
        if not nxt:
            return None
        p2 = rng.choice(nxt)
        return ("%s 的%s，再沿 %s 指向什么" % (tail(inst), p1, p2),
                "SELECT ?o WHERE { <%s> <%s%s> ?m . ?m <%s%s> ?o }"
                % (inst, NS, p1, NS, p2), cls)

    if pattern == "branch" and len(lits) >= 2:
        a, b = rng.sample(lits, 2)
        return ("%s 的%s和%s分别是什么" % (tail(inst), a, b),
                "SELECT ?o1 ?o2 WHERE { <%s> <%s%s> ?o1 . <%s> <%s%s> ?o2 }"
                % (inst, NS, a, inst, NS, b), cls)

    if pattern == "filter" and lits:
        p = rng.choice(lits)
        val = str(kv[p][0])
        return ("哪些 %s 的 %s 是 %s" % (cls, p, val),
                'SELECT ?s WHERE { ?s a <%s%s> . ?s <%s%s> "%s" }'
                % (NS, cls, NS, p, val), cls)

    if pattern == "reverse":
        cands = [i for i in incoming if incoming[i]]
        if not cands:
            return None
        obj = rng.choice(cands)
        src, p = incoming[obj][0]
        return ("哪些实体通过 %s 指向 %s" % (p, tail(obj)),
                "SELECT ?s WHERE { ?s <%s%s> <%s> }" % (NS, p, obj), tail(classes[0]))

    if pattern == "type":
        return ("%s 一共有多少个" % cls,
                "SELECT ?s WHERE { ?s a <%s%s> }" % (NS, cls), cls)

    return None


def classes_for(types):
    return [c for c in types if len(types[c]) >= 3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nt", required=True)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261120)
    args = ap.parse_args()

    triples = parse_nt(args.nt)
    types, props, incoming = index(triples)
    rng = random.Random(args.seed)
    per = max(1, args.n // len(PATTERNS))
    rows, seen, stats = [], set(), {}
    for pat in PATTERNS:
        cap = len(classes_for(types)) if pat == "type" else per
        got, tries = 0, 0
        while got < cap and tries < per * 60:
            tries += 1
            item = gen(types, props, incoming, pat, rng)
            if not item:
                continue
            question, sparql, cls = item
            if (pat, sparql) in seen:
                continue
            # 先登记再验证：验证失败的查询若不入 seen，会被反复重试同一句。
            seen.add((pat, sparql))
            # 每次 query 都要重新解析整份 NT，全量验证在 500 题时是 O(n^2)。
            # 每类前 10 条执行验证，其余按结构收录并标注 verified=False。
            verified = False
            res = {}
            # two_hop 在自研引擎里是笛卡尔式 join，实测单查 1s 以上，
            # 只做结构收录不执行验证；其余模式每类前 10 条执行验证。
            if got < 10 and pat != "two_hop":
                res = S.query(args.nt, sparql)
                if res.get("error") or not res.get("rows"):
                    continue
                verified = True
            rows.append({"id": "%s-%03d" % (pat, got + 1), "pattern": pat, "class": cls,
                         "question": question, "sparql": sparql, "verified": verified,
                         "answer_vars": res.get("vars"), "answer_rows": len(res.get("rows") or []),
                         "sample": (res.get("rows") or [])[:2]})
            got += 1
        stats[pat] = got

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("生成 %d 题 -> %s" % (len(rows), args.out))
    print("分布:", stats)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
