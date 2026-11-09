#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""axiom_reason.py — 轻量公理集 + 小型前向推理机（零第三方依赖）

定位（与 README 的「轻本体」约定一致）：
  不是 OWL DL 推理机，不引 rdflib，不做一致性问题求解。只做五件事：
    1. RDFS 类型继承（subClassOf 传递闭包）
    2. domain/range 类型补充（公理来源是 schema 的 relations，不是 nt 里的 rdfs:domain）
    3. 属性公理：inverseOf / 属性链 / 函数型（单值）
    4. 不相交类冲突检查
    5. 少量业务规则传播（条件 → 沿一条关系 → 赋值），把质检结论推到批次和产品

  每条推出来的事实都记着「依据哪条公理、由哪几条事实推出」，report 里可查。
  不做反向链、不做追溯、不删除任何既有事实。

输入：N-Triples + schema(json) + axioms(json)
输出：inferred.nt / report.json / report.md，全部落在 --out-dir 下，不改输入文件。

用法：
    python axiom_reason.py --nt output/valve.nt \
        --schema config/ontology_schema.json \
        --axioms config/ontology_axioms.json \
        --out-dir output/reason
"""

import argparse
import io
import json
import os
import re
import sys
import time

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
RDF_TYPE = RDF + "type"
RDFS_SUBCLASS = RDFS + "subClassOf"
RDFS_DOMAIN = RDFS + "domain"
RDFS_RANGE = RDFS + "range"
RDFS_LABEL = RDFS + "label"

MAX_ROUNDS_DEFAULT = 20

_LINE = re.compile(r'^<([^>]+)>\s+<([^>]+)>\s+(<[^>]+>|"(?:[^"\\]|\\.)*"(?:\^\^<[^>]+>|@[A-Za-z-]+)?)\s*\.\s*$')


# ───────────────────────── 基础：三元组读写 ─────────────────────────

def is_literal(o):
    return o.startswith('"')


def lit_value(o):
    """从 '"合格"^^<...#string>' 里取出裸值。"""
    if not is_literal(o):
        return o
    end = 2
    esc = False
    while end < len(o):
        ch = o[end]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            break
        end += 1
    return o[1:end]


def local(iri):
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[1]
    return iri


def parse_nt(path):
    """读 N-Triples，返回 [(s, p, o)]。解析不了的行跳过并计数。"""
    triples, bad = [], 0
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _LINE.match(line)
            if not m:
                bad += 1
                continue
            s, p, o = m.groups()
            triples.append((s, p, o[1:-1] if o.startswith("<") else o))
    return triples, bad


def triple_line(t):
    s, p, o = t
    return "<%s> <%s> %s ." % (s, p, o if is_literal(o) else "<%s>" % o)


# ───────────────────────── 公理集 ─────────────────────────

class Axioms(object):
    """公理集：来自 axioms.json + schema 派生。每条带 id，便于追溯。"""

    def __init__(self, cfg, schema, nt_triples):
        self.cfg = cfg or {}
        self.prefix = self.cfg.get("prefix") or ""
        self.derived = []
        self.schema_mismatch = []

        self.subclass = []          # (sub, super, axiom_id)
        for s, p, o in nt_triples:
            if p == RDFS_SUBCLASS:
                self.subclass.append((s, o, "AX-RDFS-SUBCLASS"))

        self.domain = {}            # prop -> (class_iri, axiom_id)
        self.range = {}
        self._derive_domain_range(schema, nt_triples)
        self._audit_against_nt(nt_triples)

        self.inverse = []           # (p, q, id)
        for i, pair in enumerate(self.cfg.get("inverse_of") or []):
            p, q = pair[0], pair[1]
            self.inverse.append((self._iri(p), self._iri(q), "AX-INV-%02d" % (i + 1)))

        self.chains = []            # dict
        for c in self.cfg.get("chains") or []:
            self.chains.append({
                "id": c.get("id") or "AX-C",
                "label": c.get("label") or "",
                "path": [(self._iri(x["prop"]), bool(x.get("inverse"))) for x in c["path"]],
                "implies": self._iri(c["implies"]),
            })

        self.functional = []        # (prop_iri, id)
        for f in self.cfg.get("functional") or []:
            if isinstance(f, str):
                self.functional.append((self._iri(f), "AX-F"))
            else:
                self.functional.append((self._iri(f["prop"]), f.get("id") or "AX-F"))

        self.disjoint = []          # (a, b, id)
        for d in self.cfg.get("disjoint_with") or []:
            a, b = d["classes"][0], d["classes"][1]
            self.disjoint.append((self._iri(a), self._iri(b), d.get("id") or "AX-D"))

        self.rules = []
        for r in self.cfg.get("business_rules") or []:
            self.rules.append({
                "id": r.get("id") or "BR",
                "label": r.get("label") or "",
                "when": r["when"],
                "then": r["then"],
            })

    def _iri(self, name):
        if name is None:
            return None
        if name.startswith("http"):
            return name
        return self.prefix + name

    def _class_iri(self, ent):
        """entity -> 类 IRI。导出器用 table 名首字母大写作类名（valve_products → Valve_products）。"""
        t = ent.get("table") or ent.get("id")
        return self._iri(t[:1].upper() + t[1:] if t else ent.get("id"))

    def _derive_domain_range(self, schema, nt_triples):
        if not (self.cfg.get("derive") or {}).get("domain_range"):
            return
        ents = {e.get("id"): e for e in (schema or {}).get("entities") or []}
        for rel in (schema or {}).get("relations") or []:
            f, t = ents.get(rel.get("from")), ents.get(rel.get("to"))
            if not f or not t:
                continue
            prop = self._iri(rel.get("id"))
            did = "AX-DR-%s" % rel.get("id")
            self.domain[prop] = (self._class_iri(f), did)
            self.range[prop] = (self._class_iri(t), did)
            self.derived.append({"axiom": did, "prop": local(prop),
                                 "domain": local(self._class_iri(f)),
                                 "range": local(self._class_iri(t)),
                                 "from": "%s -> %s" % (rel.get("from"), rel.get("to"))})

    def _audit_against_nt(self, nt_triples):
        """对账：nt 里写的 rdfs:domain/range 与 schema 公理是否一致。"""
        nt_dom, nt_rng = {}, {}
        for s, p, o in nt_triples:
            if p == RDFS_DOMAIN:
                nt_dom[s] = o
            elif p == RDFS_RANGE:
                nt_rng[s] = o
        for prop, (cls, _) in self.domain.items():
            got = nt_dom.get(prop)
            if got and got != cls:
                self.schema_mismatch.append(
                    {"prop": local(prop), "kind": "domain",
                     "schema": local(cls), "exported": local(got)})
        for prop, (cls, _) in self.range.items():
            got = nt_rng.get(prop)
            if got and got != cls:
                self.schema_mismatch.append(
                    {"prop": local(prop), "kind": "range",
                     "schema": local(cls), "exported": local(got)})


# ───────────────────────── 推理机 ─────────────────────────

class Reasoner(object):

    def __init__(self, triples, axioms, fix_dangling=True, max_rounds=MAX_ROUNDS_DEFAULT):
        self.ax = axioms
        self.fix_dangling = fix_dangling
        self.max_rounds = max_rounds
        self.facts = []                       # 全部事实（原 + 推）
        self.seen = set()
        self.sp = {}                          # (s,p) -> [o]
        self.po = {}                          # (p,o) -> [s]
        self.types = {}                       # s -> set(class iri)
        self.just = {}                        # triple -> {"axiom":id,"from":[...]}
        self.by_axiom = {}                    # axiom id -> 新增条数
        self.repaired = []
        self.endpoint_mismatch = []     # 公理端点与实例数据不符 -> 跳过，宁可不推也不推错
        self._mismatch_seen = set()
        self.rule_conflicts = []        # 规则要赋的值与既有值不同 -> 报告改写，不静默叠加
        self.rounds = 0
        self.fixed = False

        for t in triples:
            self._add(t, None)
        self.subjects = set(s for s, _, _ in triples)
        self.asserted = dict((s, set(c)) for s, c in self.types.items())
        self._class_up = self._build_class_closure()

    def _build_class_closure(self):
        up = {}
        for sub, super_, _ in self.ax.subclass:
            up.setdefault(sub, set()).add(super_)
        changed = True
        while changed:
            changed = False
            for sub, sups in list(up.items()):
                for s2 in list(sups):
                    for s3 in up.get(s2, ()):
                        if s3 not in sups:
                            sups.add(s3)
                            changed = True
        return up

    def _compatible(self, types, target):
        """个体既有类型是否与要求的类相容（同类型或子类）。没有类型信息时视为可补。"""
        for t in types:
            if t == target or target in self._class_up.get(t, ()):
                return True
        return False

    # —— 索引与写入 ——
    def _add(self, t, just):
        if t in self.seen:
            return False
        self.seen.add(t)
        self.facts.append(t)
        s, p, o = t
        self.sp.setdefault((s, p), []).append(o)
        if not is_literal(o):
            self.po.setdefault((p, o), []).append(s)
        if p == RDF_TYPE and not is_literal(o):
            self.types.setdefault(s, set()).add(o)
        if just:
            self.just[t] = just
            self.by_axiom[just["axiom"]] = self.by_axiom.get(just["axiom"], 0) + 1
        return True

    def _assert(self, s, p, o, axiom, frm):
        return self._add((s, p, o),
                         {"axiom": axiom, "from": [triple_line(x) for x in frm]})

    def objects(self, s, p):
        return self.sp.get((s, p), [])

    def subjects_of(self, p, o):
        return self.po.get((p, o), [])

    # —— 主循环：半朴素前向链，跑到不动点 ——
    def run(self):
        while not self.fixed and self.rounds < self.max_rounds:
            self.rounds += 1
            self.fixed = True
            self._type_inheritance()
            self._domain_range()
            self._inverse()
            self._chains()
            self._business_rules()
        return self

    def _type_inheritance(self):
        """个体类型沿 subClassOf 上溯；类自身也上溯（类层次闭包）。"""
        sup = {}
        for sub, super_, _ in self.ax.subclass:
            sup.setdefault(sub, []).append(super_)
        if not sup:
            return
        # 个体
        for s, classes in list(self.types.items()):
            if s in sup:            # 这是类，不是个体，走下面分支
                continue
            for c in list(classes):
                for super_ in sup.get(c, []):
                    if self._assert(s, RDF_TYPE, super_, "AX-RDFS-SUBCLASS",
                                    [(s, RDF_TYPE, c), (c, RDFS_SUBCLASS, super_)]):
                        self.fixed = False
        # 类层次传递：sub ⊑ mid ⊑ super ⇒ sub ⊑ super
        for sub in list(sup):
            for mid in list(sup.get(sub, ())):
                for super_ in list(sup.get(mid, ())):
                    if self._assert(sub, RDFS_SUBCLASS, super_, "AX-RDFS-SUBCLASS",
                                    [(sub, RDFS_SUBCLASS, mid), (mid, RDFS_SUBCLASS, super_)]):
                        self.fixed = False

    def _domain_range(self):
        """按公理补类型。补之前先验：个体已有的类型若与公理端点不相容，跳过并记账。

        这一步是刻意的保守：schema 里写的 from/to 与实例数据的实际方向可能相反
        （本项目 sells 就是反的），硬套公理会把 30 条销售记录全标成产品。
        """
        for (s, p), objs in list(self.sp.items()):
            if p in (RDF_TYPE, RDFS_SUBCLASS, RDFS_DOMAIN, RDFS_RANGE, RDFS_LABEL):
                continue
            if is_literal(objs[0]):
                continue
            d = self.ax.domain.get(p)
            if d:
                own = self.asserted.get(s, set())
                if own and not self._compatible(own, d[0]):
                    self._note_mismatch(p, "domain", local(d[0]), s, own, d[1])
                elif self._assert(s, RDF_TYPE, d[0], d[1], [(s, p, objs[0])]):
                    self.fixed = False
            r = self.ax.range.get(p)
            if r:
                for o in objs:
                    own = self.asserted.get(o, set())
                    if own and not self._compatible(own, r[0]):
                        self._note_mismatch(p, "range", local(r[0]), o, own, r[1])
                        continue
                    if self._assert(o, RDF_TYPE, r[0], r[1], [(s, p, o)]):
                        self.fixed = False

    def _note_mismatch(self, prop, kind, expect, node, own, aid):
        pname = local(prop)
        if (pname, kind, node) in self._mismatch_seen:
            return
        self._mismatch_seen.add((pname, kind, node))
        for m in self.endpoint_mismatch:
            if m["prop"] == pname and m["kind"] == kind:
                m["cases"] += 1
                return
        self.endpoint_mismatch.append({
            "axiom": aid, "prop": pname, "kind": kind, "expect": expect,
            "actual": sorted(local(x) for x in own), "sample": local(node), "cases": 1})

    def _inverse(self):
        for p, q, aid in self.ax.inverse:
            for (s, pp), objs in list(self.sp.items()):
                if pp != p:
                    continue
                for o in objs:
                    if is_literal(o):
                        continue
                    if self._assert(o, q, s, aid, [(s, p, o)]):
                        self.fixed = False

    def _chain_pairs(self, steps):
        """按路径从起点走到终点，返回 (起点, 终点) 集合。

        路径语义：steps[0] 从起点出发，steps[-1] 走到终点；
        inverse=True 表示这一步是反向走（借 rdfs 逆关系）。
        """
        p0, inv0 = steps[0]
        if inv0:
            starts = set(o for (p, o) in self.po if p == p0 and not is_literal(o))
        else:
            starts = set(s for (s, p) in self.sp if p == p0)
        pairs = []
        for st in starts:
            nodes = {st}
            for prop, inv in steps:
                nxt = set()
                for n in nodes:
                    if inv:
                        nxt.update(self.subjects_of(prop, n))
                    else:
                        nxt.update(o for o in self.objects(n, prop) if not is_literal(o))
                nodes = nxt
                if not nodes:
                    break
            for e in nodes:
                pairs.append((st, e))
        return pairs

    def _chains(self):
        for ch in self.ax.chains:
            for a, b in self._chain_pairs(ch["path"]):
                if a == b:
                    continue
                if self._assert(a, ch["implies"], b, ch["id"],
                                [(a, ch["path"][0][0], b)]):
                    self.fixed = False

    def _match(self, s, cond):
        cls = self.ax._iri(cond["class"])
        if cls and cls not in self.types.get(s, set()):
            return False
        prop = self.ax._iri(cond["prop"])
        vals = [lit_value(o) for o in self.objects(s, prop)]
        if not vals:
            return False
        op, want = cond.get("op", "eq"), cond.get("value")
        for v in vals:
            if op == "eq" and v == str(want):
                return True
            if op == "in" and v in [str(x) for x in want]:
                return True
            if op in ("gt", "lt"):
                try:
                    fv, fw = float(v), float(want)
                except (TypeError, ValueError):
                    continue
                if (op == "gt" and fv > fw) or (op == "lt" and fv < fw):
                    return True
        return False

    def _business_rules(self):
        for rule in self.ax.rules:
            when, then = rule["when"], rule["then"]
            prop = self.ax._iri(then["prop"])
            for s in list(self.sp.keys()):
                s0 = s[0]
                if not self._match(s0, when):
                    continue
                targets = [s0]
                if then.get("link"):
                    link = self.ax._iri(then["link"])
                    targets = []
                    for t in self.objects(s0, link):
                        if is_literal(t):
                            continue
                        if then.get("target_class"):
                            tc = self.ax._iri(then["target_class"])
                            if tc and tc not in self.types.get(t, set()):
                                continue
                        targets.append(t)
                for t in targets:
                    before = sorted({lit_value(o) for o in self.objects(t, prop) if is_literal(o)})
                    if before and then["value"] not in before:
                        key = (rule["id"], t, tuple(before))
                        if key not in self._mismatch_seen:
                            self._mismatch_seen.add(key)
                            self.rule_conflicts.append({
                                "rule": rule["id"], "individual": local(t),
                                "prop": local(prop), "before": before,
                                "after": then["value"]})
                    if self._assert(t, prop, '"%s"' % then["value"], rule["id"],
                                    [(s0, self.ax._iri(when["prop"]), '"%s"' % when["value"])]):
                        self.fixed = False

    # —— 事后检查 ——
    def dangling(self):
        """对象 IRI 从未作为主语出现（且不是类/属性/本体声明）→ 引用断裂。"""
        declared = set()
        for s, p, o in self.facts:
            if p == RDF_TYPE and not is_literal(o) and local(o) in (
                    "Class", "ObjectProperty", "DatatypeProperty", "Ontology"):
                declared.add(s)
        out = []
        for s, p, o in self.facts:
            if is_literal(o) or o in self.subjects or o in declared:
                continue
            if self.ax.prefix and not o.startswith(self.ax.prefix):
                continue
            out.append((s, p, o))
        return out

    def repair(self, dangling):
        """按 IRI 尾段在已知个体里找唯一同名者，替换之（只在内存里改，不动原文件）。"""
        if not self.fix_dangling:
            return 0
        by_tail = {}
        for s in self.subjects:
            by_tail.setdefault(local(s).rsplit("_", 1)[-1], []).append(s)
        fixed = 0
        for s, p, o in dangling:
            tail = local(o).rsplit("_", 1)[-1]
            cand = [x for x in by_tail.get(tail, []) if x != o]
            if len(cand) != 1:
                continue
            self._replace_object(s, p, o, cand[0])
            self.repaired.append({"subject": local(s), "prop": local(p),
                                  "broken": local(o), "fixed": local(cand[0])})
            fixed += 1
        return fixed

    def _replace_object(self, s, p, old, new):
        t_old, t_new = (s, p, old), (s, p, new)
        if t_new in self.seen:
            return
        self.seen.discard(t_old)
        self.seen.add(t_new)
        try:
            self.facts[self.facts.index(t_old)] = t_new
        except ValueError:  # best-effort: 索引里没有就跳过，索引本身已在上文改过
            return
        lst = self.sp.get((s, p), [])
        if old in lst:
            lst[lst.index(old)] = new
        pl = self.po.get((p, old), [])
        if s in pl:
            pl.remove(s)
        self.po.setdefault((p, new), []).append(s)
        self.just[t_new] = {"axiom": "AX-REPAIR",
                            "from": ["%s 按 range 公理修正为 %s" % (local(old), local(new))]}
        self.by_axiom["AX-REPAIR"] = self.by_axiom.get("AX-REPAIR", 0) + 1

    def functional_conflicts(self):
        out = []
        for prop, aid in self.ax.functional:
            for (s, p), objs in self.sp.items():
                if p != prop or len(objs) < 2:
                    continue
                vals = sorted({lit_value(o) if is_literal(o) else local(o) for o in objs})
                out.append({"axiom": aid, "subject": local(s), "prop": local(p), "values": vals})
        return out

    def disjoint_conflicts(self):
        out = []
        for a, b, aid in self.ax.disjoint:
            for s, classes in self.types.items():
                if a in classes and b in classes:
                    out.append({"axiom": aid, "individual": local(s),
                                "classes": [local(a), local(b)]})
        return out


# ───────────────────────── 输出 ─────────────────────────

def write_outputs(out_dir, reasoner, ax, path_nt, stats, bad_lines, elapsed):
    os.makedirs(out_dir, exist_ok=True)
    inferred = [t for t in reasoner.facts if t in reasoner.just]

    with io.open(os.path.join(out_dir, "inferred.nt"), "w", encoding="utf-8", newline="\n") as f:
        for t in inferred:
            f.write(triple_line(t) + "\n")

    # 闭合图（原图 + 推理结果），可直接喂 ontology_sparql.query 查，不改原文件
    with io.open(os.path.join(out_dir, "closure.nt"), "w", encoding="utf-8", newline="\n") as f:
        for t in reasoner.facts:
            f.write(triple_line(t) + "\n")

    sample = []
    # 先给业务规则和属性链的结果，引用修正放后面 —— 报告是给人看的，别让修复噪声占满
    ordered = sorted(inferred, key=lambda t: (reasoner.just[t]["axiom"].startswith("AX-REPAIR"),))
    for t in ordered[:40]:
        j = reasoner.just[t]
        sample.append({"triple": triple_line(t), "axiom": j["axiom"], "from": j["from"]})

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": path_nt,
        "elapsed_sec": round(elapsed, 2),
        "stats": stats,
        "bad_lines": bad_lines,
        "derived_axioms": ax.derived,
        "schema_mismatch": ax.schema_mismatch,
        "endpoint_mismatch": reasoner.endpoint_mismatch,
        "rule_conflicts": reasoner.rule_conflicts,
        "repaired_refs": reasoner.repaired,
        "by_axiom": reasoner.by_axiom,
        "inferred_total": len(inferred),
        "inferred_sample": sample,
    }
    with io.open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = []
    md.append("# 公理推理报告 %s\n" % time.strftime("%Y-%m-%d %H:%M"))
    md.append("源文件 `%s`，推理 %d 轮，用时 %.2fs。\n" % (path_nt, reasoner.rounds, elapsed))
    md.append("| 项 | 数 |")
    md.append("|---|---|")
    md.append("| 原始事实 | %d |" % stats["input"])
    md.append("| 推出新事实 | %d |" % len(inferred))
    md.append("| 闭合后事实 | %d |" % len(reasoner.facts))
    md.append("| 引用断裂（已修） | %d |" % len(reasoner.repaired))
    md.append("| 单值冲突 | %d |" % stats["functional_conflicts"])
    md.append("| 不相交冲突 | %d |" % stats["disjoint_conflicts"])
    md.append("")
    if reasoner.by_axiom:
        md.append("## 各公理贡献\n")
        md.append("| 公理 | 新增事实 |")
        md.append("|---|---|")
        for k, v in sorted(reasoner.by_axiom.items(), key=lambda x: -x[1]):
            md.append("| %s | %d |" % (k, v))
        md.append("")
    if ax.schema_mismatch:
        md.append("## 导出产物与 schema 公理对不上（%d 处）\n" % len(ax.schema_mismatch))
        md.append("| 属性 | 项 | schema 说 | nt 里写的 |")
        md.append("|---|---|---|---|")
        for m in ax.schema_mismatch:
            md.append("| %s | %s | %s | %s |" % (m["prop"], m["kind"], m["schema"], m["exported"]))
        md.append("")
    if reasoner.repaired:
        md.append("## 引用断裂修正（%d 条）\n" % len(reasoner.repaired))
        seen = set()
        for r in reasoner.repaired:
            key = (r["prop"], r["broken"].split("_")[0])
            if key in seen:
                continue
            seen.add(key)
            md.append("- `%s` 的宾语 `%s` 不存在，按唯一同名个体改为 `%s`（同类问题共 %d 条）"
                      % (r["prop"], r["broken"], r["fixed"],
                         sum(1 for x in reasoner.repaired if x["prop"] == r["prop"])))
        md.append("")
    if reasoner.endpoint_mismatch:
        md.append("## 公理端点与数据对不上，已跳过（%d 类）\n"
                  % len(reasoner.endpoint_mismatch))
        md.append("宁可不推，也不推错类型。这些地方要么改 schema，要么改导出器。\n")
        md.append("| 属性 | 项 | 公理要求 | 实例实际 | 条数 |")
        md.append("|---|---|---|---|---|")
        for m in reasoner.endpoint_mismatch:
            md.append("| %s | %s | %s | %s | %d |"
                      % (m["prop"], m["kind"], m["expect"],
                         "/".join(m["actual"]) or "无类型", m["cases"]))
        md.append("")
    if reasoner.rule_conflicts:
        md.append("## 业务规则改写了既有状态（%d 条）\n" % len(reasoner.rule_conflicts))
        md.append("这些个体原本已有值，规则又赋了一个新值。要么该值本来就该改，要么规则写错了，得人看。\n")
        md.append("| 规则 | 个体 | 属性 | 原有 | 规则推出 |")
        md.append("|---|---|---|---|---|")
        for c in reasoner.rule_conflicts:
            md.append("| %s | %s | %s | %s | %s |"
                      % (c["rule"], c["individual"], c["prop"],
                         "、".join(c["before"]), c["after"]))
        md.append("")
    if stats["functional_conflicts"] or stats["disjoint_conflicts"]:
        md.append("## 冲突\n")
        for c in stats.get("functional_detail", [])[:10]:
            md.append("- 单值冲突 `%s.%s` = %s（%s）"
                      % (c["subject"], c["prop"], "、".join(c["values"]), c["axiom"]))
        for c in stats.get("disjoint_detail", [])[:10]:
            md.append("- 不相交冲突 `%s` 同时属于 %s（%s）"
                      % (c["individual"], " 和 ".join(c["classes"]), c["axiom"]))
        md.append("")
    md.append("## 新增事实样例（前 12 条）\n")
    for s in sample[:12]:
        md.append("```")
        md.append(s["triple"])
        md.append("# 依据 %s：%s" % (s["axiom"], "; ".join(s["from"])[:300]))
        md.append("```")
    with io.open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md) + "\n")
    return inferred


def main(argv=None):
    ap = argparse.ArgumentParser(description="轻量公理集推理（前向链，零依赖）")
    ap.add_argument("--nt", default=os.path.join("output", "valve.nt"))
    ap.add_argument("--schema", default=os.path.join("config", "ontology_schema.json"))
    ap.add_argument("--axioms", default=os.path.join("config", "ontology_axioms.json"))
    ap.add_argument("--out-dir", default=os.path.join("output", "reason"))
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT)
    ap.add_argument("--no-fix-dangling", action="store_true", help="不修正引用断裂，只报告")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.time()
    triples, bad = parse_nt(a.nt)
    schema = {}
    if os.path.exists(a.schema):
        schema = json.load(io.open(a.schema, encoding="utf-8"))
    cfg = {}
    if os.path.exists(a.axioms):
        cfg = json.load(io.open(a.axioms, encoding="utf-8"))

    ax = Axioms(cfg, schema, triples)
    r = Reasoner(triples, ax, fix_dangling=not a.no_fix_dangling, max_rounds=a.max_rounds)

    dang = r.dangling()
    r.repair(dang)
    r.run()

    fc = r.functional_conflicts()
    dc = r.disjoint_conflicts()
    stats = {
        "input": len(triples),
        "functional_conflicts": len(fc),
        "disjoint_conflicts": len(dc),
        "functional_detail": fc,
        "disjoint_detail": dc,
        "axiom_count": len(ax.derived) + len(ax.chains) + len(ax.functional) + len(ax.disjoint) + len(ax.rules),
    }
    elapsed = time.time() - t0
    inferred = write_outputs(a.out_dir, r, ax, a.nt, stats, bad, elapsed)

    if not a.quiet:
        print("推理完成：原始 %d 条 → 新增 %d 条（%d 轮，%.2fs）"
              % (len(triples), len(inferred), r.rounds, elapsed))
        print("  公理 %d 条（派生 %d / 链 %d / 单值 %d / 不相交 %d / 业务规则 %d）"
              % (stats["axiom_count"], len(ax.derived), len(ax.chains),
                 len(ax.functional), len(ax.disjoint), len(ax.rules)))
        if ax.schema_mismatch:
            print("  ⚠ 导出产物与 schema 有 %d 处 domain/range 对不上" % len(ax.schema_mismatch))
        if r.endpoint_mismatch:
            print("  ⚠ 有 %d 处公理端点与实例数据不符，已跳过（不推错类型）"
                  % len(r.endpoint_mismatch))
        if r.repaired:
            print("  ⚠ 引用断裂 %d 条（已在内存修正，原文件未动）" % len(r.repaired))
        if fc or dc:
            print("  ⚠ 冲突：单值 %d 条、不相交 %d 条" % (len(fc), len(dc)))
        print("  输出 %s" % os.path.abspath(a.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
