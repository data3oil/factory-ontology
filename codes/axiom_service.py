#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""axiom_service.py — 公理集的服务层：分库存取、跨行业种子生成、跑推理、给问答供答案。

三层职责，别混：
  1. 存取：每一行业库一份 config/axioms/<kb>.json。前端读写的就是这份文件。
  2. 生成：库里没有公理文件时，按 _seed.json 的「角色」认表（产品/批次/原料/质检），
     再沿**数据里真实存在的边**找路径，生成初始公理。种子不写死任何一行的表名。
  3. 推理与作答：跑 axiom_reason 得到 closure，然后用公理自带的 ask.terms 接自然语言，
     答案带依据（哪条公理、由哪几条事实推出）。

读写都不碰 nt / 词典 / 规则引擎；推理产物落在 output/reason/<kb>/（已 gitignore）。
"""

import io
import json
import os
import re
import time

import axiom_reason as ar

ROOT = os.path.dirname(os.path.abspath(__file__))
AX_DIR = os.path.join(ROOT, "config", "axioms")
SEED_FILE = os.path.join(AX_DIR, "_seed.json")
KBS_FILE = os.path.join(ROOT, "config", "kbs.json")
REASON_DIR = os.path.join(ROOT, "output", "reason")

RDF_TYPE = ar.RDF_TYPE
RDFS_LABEL = ar.RDFS_LABEL
_CLASS_TAGS = ("Class", "ObjectProperty", "DatatypeProperty", "Ontology")
_SKIP_PROPS = (ar.RDF_TYPE, ar.RDFS_SUBCLASS, ar.RDFS_DOMAIN, ar.RDFS_RANGE, RDFS_LABEL)


# ───────────────────────── 路径与注册表 ─────────────────────────

def load_kbs():
    try:
        with io.open(KBS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("kbs") or d
    except (OSError, ValueError):
        return {}


def kb_conf(kb):
    return (load_kbs() or {}).get(kb) or {}


def nt_path(kb):
    c = kb_conf(kb)
    rel = c.get("nt") or os.path.join("output", "%s.nt" % kb)
    return os.path.join(ROOT, rel)


def schema_path(kb):
    """对齐 api_server._kb_ontology_schema 的三步约定，只是不反向依赖上帝模块。"""
    p = os.path.join(ROOT, "config", "ontology_schema_%s.json" % kb)
    if os.path.exists(p):
        return p
    act = os.path.join(ROOT, "config", "active_ontology.json")
    try:
        with io.open(act, encoding="utf-8") as f:
            if (json.load(f) or {}).get("kb") == kb:
                base = os.path.join(ROOT, "config", "ontology_schema.json")
                if os.path.exists(base):
                    return base
    except (OSError, ValueError):
        pass
    base = os.path.join(ROOT, "config", "ontology_schema.json")
    return base if os.path.exists(base) else None


def axiom_path(kb):
    return os.path.join(AX_DIR, "%s.json" % kb)


def reason_dir(kb):
    return os.path.join(REASON_DIR, kb)


def load_seed():
    try:
        with io.open(SEED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ───────────────────────── 存取 ─────────────────────────

def load(kb):
    """读该库公理；没有就按种子生成并落盘（只生成一次，之后以文件为准）。"""
    p = axiom_path(kb)
    if os.path.exists(p):
        try:
            with io.open(p, encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict):
                doc.setdefault("kb", kb)
                return doc
        except (OSError, ValueError):
            pass
    doc = generate(kb)
    if doc.get("chains") or doc.get("business_rules") or doc.get("functional"):
        save(kb, doc, backup=False)
    return doc


def validate(doc):
    """结构校验。宁可拒绝保存，也不让一个坏文件把问答链路带崩。"""
    if not isinstance(doc, dict):
        return "公理集必须是 JSON 对象"
    seen = set()
    for key in ("chains", "inverse_of", "functional", "disjoint_with", "business_rules"):
        val = doc.get(key)
        if val is not None and not isinstance(val, list):
            return "%s 必须是数组" % key
    for ch in doc.get("chains") or []:
        if not isinstance(ch, dict) or not ch.get("implies") or not ch.get("path"):
            return "属性链缺少 implies 或 path"
        if not isinstance(ch["path"], list) or not ch["path"]:
            return "属性链 path 必须是非空数组"
        for step in ch["path"]:
            if not isinstance(step, dict) or not step.get("prop"):
                return "属性链每一步都要有 prop"
        aid = ch.get("id") or ch["implies"]
        if aid in seen:
            return "公理 id 重复：%s" % aid
        seen.add(aid)
    for br in doc.get("business_rules") or []:
        if not isinstance(br, dict) or not br.get("when") or not br.get("then"):
            return "业务规则缺少 when 或 then"
        if not br["then"].get("prop") or br["then"].get("value") in (None, ""):
            return "业务规则 then 缺 prop 或 value"
    for f in doc.get("functional") or []:
        if isinstance(f, dict) and not f.get("prop"):
            return "函数型公理缺 prop"
        if not isinstance(f, dict) and not isinstance(f, str):
            return "函数型公理格式不对"
    for d in doc.get("disjoint_with") or []:
        cls = d.get("classes") if isinstance(d, dict) else d
        if not isinstance(cls, list) or len(cls) != 2:
            return "不相交公理必须是两个类的组合"
    return None


def save(kb, doc, backup=True):
    err = validate(doc)
    if err:
        return False, err
    os.makedirs(AX_DIR, exist_ok=True)
    p = axiom_path(kb)
    # 内容等价就不写盘：面板"打开→保存"传回来的是同一份文档，但 json.dump 会
    # 重排缩进、抹掉人手写的空行，一保存就制造几十上百行无意义 diff。
    # 语义没变就当成功直接返回，连备份和 updated_at 都不动。
    if os.path.exists(p):
        try:
            with io.open(p, encoding="utf-8") as f:
                old = json.load(f)
            a = {k: v for k, v in old.items() if k != "updated_at"}
            b = {k: v for k, v in doc.items() if k != "updated_at"}
            b.setdefault("kb", kb)
            if a == b:
                return True, None
        except (ValueError, OSError):
            pass  # 旧文件坏了/读不了：照常走覆盖 + 备份，不做"等价"判断
    if backup and os.path.exists(p):
        try:
            bak = p + ".bak_%s" % time.strftime("%Y%m%d_%H%M%S")
            with io.open(p, encoding="utf-8") as src, io.open(bak, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        except OSError:
            pass
    doc.setdefault("kb", kb)
    doc["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return True, None


# ───────────────────────── 种子生成 ─────────────────────────

def _triples(kb):
    p = nt_path(kb)
    if not os.path.exists(p):
        return [], None
    triples, _bad = ar.parse_nt(p)
    return triples, p


def detect_prefix(triples):
    """从数据里取命名空间：出现最多的 <...># 前缀。"""
    cnt = {}
    for s, _p, _o in triples[:4000]:
        if "#" in s:
            pre = s.rsplit("#", 1)[0] + "#"
            cnt[pre] = cnt.get(pre, 0) + 1
    if not cnt:
        return ""
    return max(cnt.items(), key=lambda x: x[1])[0]


def _classes(triples):
    """{类本地名: 计数}，来自 rdf:type ... Class。"""
    out = {}
    for s, p, o in triples:
        if p == RDF_TYPE and o.endswith("#Class"):
            out[s.rsplit("#", 1)[-1]] = out.get(s.rsplit("#", 1)[-1], 0) + 1
    return out


def _individual_types(triples, classes):
    """{个体 IRI: 类本地名}（只认一级类，兼容按表名派生的类）。"""
    out = {}
    for s, p, o in triples:
        if p != RDF_TYPE:
            continue
        name = o.rsplit("#", 1)[-1]
        if name in classes:
            out.setdefault(s, name)
    return out


def _role_of(name, roles):
    """按角色关键词认表。带 __ 的是状态/子类细分，不参与角色识别。"""
    if not name or "__" in name:
        return None
    low = name.lower()
    best = None
    for role, spec in (roles or {}).items():
        for token in spec.get("match") or []:
            if token.lower() in low:
                if best is None or len(token) > len(best[1]):
                    best = (role, token)
    return best[0] if best else None


def _observed_edges(triples, types):
    """类级边：{(源类, 属性, 目标类): 次数}，只看对象是 IRI 的三元组。"""
    edges = {}
    for s, p, o in triples:
        if p in _SKIP_PROPS or ar.is_literal(o):
            continue
        cs, co = types.get(s), types.get(o)
        if not cs or not co or cs == co:
            continue
        key = (cs, p.rsplit("#", 1)[-1], co)
        edges[key] = edges.get(key, 0) + 1
    return edges


def _find_path(edges, src, dst, max_steps):
    """在类级边上找 src→dst 的最短路径，反向走也算。

    返回 [(属性, 是否反向)]；找不到返回 None。
    """
    if src == dst:
        return []
    fwd, back = {}, {}
    for (a, p, b), n in edges.items():
        fwd.setdefault(a, []).append((p, b, False, n))
        back.setdefault(b, []).append((p, a, True, n))
    frontier = [(src, [])]
    seen = {src}
    for _ in range(max_steps):
        nxt = []
        for node, path in frontier:
            for p, other, inv, n in fwd.get(node, []) + back.get(node, []):
                if other in seen:
                    continue
                step = (p, inv)
                if other == dst:
                    return path + [step]
                seen.add(other)
                nxt.append((other, path + [step]))
        frontier = nxt
        if not frontier:
            break
    return None


def _attr_values(triples):
    """{个体: {属性: [字面量值]}}，用于从数据里认质检取值。"""
    out = {}
    for s, p, o in triples:
        if not ar.is_literal(o):
            continue
        out.setdefault(s, {}).setdefault(p.rsplit("#", 1)[-1], []).append(ar.lit_value(o))
    return out


def generate(kb):
    """按种子 + 该库数据，生成一份可编辑的初始公理集。"""
    seed = load_seed()
    triples, _ntp = _triples(kb)
    prefix = detect_prefix(triples) or "http://factory.example/%s#" % kb
    doc = {
        "version": "0.1.0",
        "label": "%s 公理集（种子生成）" % kb,
        "kb": kb,
        "prefix": prefix,
        "note": "初始由跨行业种子按角色识别生成，路径取自该库数据里真实存在的边。可以随便改，改完点保存再跑推理。",
        "derive": {"domain_range": True, "type_inheritance": True},
        "inverse_of": [],
        "chains": [],
        "functional": [],
        "disjoint_with": [],
        "business_rules": [],
        "seed_report": {"roles": {}, "skipped": []},
    }
    if not triples:
        doc["seed_report"]["skipped"].append("没有读到 nt 数据，只生成了空公理集")
        return doc

    classes = _classes(triples)
    types = _individual_types(triples, classes)
    edges = _observed_edges(triples, types)

    # 角色识别
    role_classes = {}
    for cls in classes:
        if not types or cls not in set(types.values()):
            continue
        role = _role_of(cls, seed.get("roles"))
        if role:
            role_classes.setdefault(role, []).append(cls)
    for r, cs in role_classes.items():
        cs.sort()
        doc["seed_report"]["roles"][r] = cs

    # 属性链：从源角色到目标角色找路径
    for spec in seed.get("chains") or []:
        srcs = role_classes.get(spec.get("from_role")) or []
        dsts = role_classes.get(spec.get("to_role")) or []
        found = None
        for s in srcs:
            for d in dsts:
                path = _find_path(edges, s, d, int(spec.get("max_steps") or 3))
                if path:
                    found = (s, d, path)
                    break
            if found:
                break
        if not found:
            doc["seed_report"]["skipped"].append(
                "%s（%s→%s）：数据里找不到连通的边" % (spec.get("label"), spec.get("from_role"),
                                                spec.get("to_role")))
            continue
        s, d, path = found
        doc["chains"].append({
            "id": spec.get("id"),
            "label": spec.get("label"),
            "definition": "由种子按 %s → %s 的实际路径生成" % (s, d),
            "path": [{"prop": p, "inverse": inv} for p, inv in path],
            "implies": spec.get("implies"),
            "ask": spec.get("ask") or {},
            "from_class": s,
            "to_class": d,
        })

    # 函数型：属性名像编号的
    hint = seed.get("functional_hints") or {}
    tokens = [t.lower() for t in hint.get("match") or []]
    seen_props = set()
    for s, p, o in triples:
        if not ar.is_literal(o):
            continue
        name = p.rsplit("#", 1)[-1]
        if name in seen_props or not any(t in name.lower() for t in tokens):
            continue
        seen_props.add(name)
        doc["functional"].append({"id": "SEED-F-%s" % name, "prop": name,
                                  "label": "%s 单值" % name,
                                  "definition": "属性名像编号，按单值处理，多值即数据冲突"})

    # 不相交：不同角色、同级、有实例的类两两不相交
    role_list = sorted(role_classes.items())
    for i in range(len(role_list)):
        for j in range(i + 1, len(role_list)):
            ra, ca = role_list[i]
            rb, cb = role_list[j]
            doc["disjoint_with"].append({
                "id": "SEED-D-%s-%s" % (ra, rb),
                "classes": [ca[0], cb[0]],
                "label": "%s 与 %s 不相交" % (ra, rb)})

    # 质检规则：数据里真读到「不合格」才生成，属性名和取值都从数据取
    qc = seed.get("qc_rule") or {}
    bad = [str(x) for x in qc.get("bad_values") or []]
    qc_classes = set(role_classes.get("qc") or [])
    values = _attr_values(triples)
    hit_prop, hit_val, hit_ind = None, None, None
    for ind, attrs in values.items():
        if types.get(ind) not in qc_classes:
            continue
        for prop, vals in attrs.items():
            for v in vals:
                if v in bad:
                    hit_prop, hit_val, hit_ind = prop, v, ind
                    break
            if hit_prop:
                break
        if hit_prop:
            break
    if hit_prop:
        target_role = "batch" if role_classes.get("batch") else None
        link = None
        if target_role:
            for (a, p, b), _n in edges.items():
                if a in qc_classes and b in role_classes[target_role]:
                    link = p
                    break
        if not link:
            # 没有「质检→被检对象」的边，规则只能落在质检记录自己身上，没有意义。
            # 与其生成一条推不出东西的规则，不如留空并说清原因，让人补关系或手写规则。
            doc["seed_report"]["skipped"].append(
                "质检规则：数据里没有「质检记录 → 批次」的边，规则无处可落，留空等人工填")
        if link and target_role:
            doc["business_rules"].append({
                "id": qc.get("id"), "label": qc.get("label"),
                "definition": "数据里 %s 属性出现了「%s」，据此生成" % (hit_prop, hit_val),
                "when": {"class": types.get(hit_ind), "prop": hit_prop, "op": "eq", "value": hit_val},
                "then": {"link": link, "target_class": role_classes[target_role][0],
                         "prop": qc.get("then_prop") or "status",
                         "value": qc.get("then_value") or "待处理"},
                "ask": qc.get("ask") or {}})
    else:
        doc["seed_report"]["skipped"].append("质检规则：数据里没有读到合格/不合格这类取值，留空等人工填")

    return doc


# ───────────────────────── 推理 ─────────────────────────

def run(kb, fix_dangling=True):
    """跑推理，产物写 output/reason/<kb>/，返回摘要。"""
    doc = load(kb)
    triples, ntp = _triples(kb)
    if not triples:
        return {"ok": False, "error": "该库没有 nt 数据：%s" % ntp}
    sp = schema_path(kb)
    schema = {}
    if sp and os.path.exists(sp):
        try:
            with io.open(sp, encoding="utf-8") as f:
                schema = json.load(f)
        except (OSError, ValueError):
            schema = {}
    ax = ar.Axioms(doc, schema, triples)
    r = ar.Reasoner(triples, ax, fix_dangling=fix_dangling)
    r.repair(r.dangling())
    r.run()

    out = reason_dir(kb)
    os.makedirs(out, exist_ok=True)
    inferred = [t for t in r.facts if t in r.just]
    with io.open(os.path.join(out, "inferred.nt"), "w", encoding="utf-8", newline="\n") as f:
        for t in inferred:
            f.write(ar.triple_line(t) + "\n")
    with io.open(os.path.join(out, "closure.nt"), "w", encoding="utf-8", newline="\n") as f:
        for t in r.facts:
            f.write(ar.triple_line(t) + "\n")

    fc, dc = r.functional_conflicts(), r.disjoint_conflicts()
    report = {
        "kb": kb,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input": len(triples),
        "inferred": len(inferred),
        "closed": len(r.facts),
        "rounds": r.rounds,
        "by_axiom": r.by_axiom,
        "repaired": len(r.repaired),
        "schema_mismatch": ax.schema_mismatch,
        "endpoint_mismatch": r.endpoint_mismatch,
        "prefix_fallbacks": ax.prefix_fallbacks,
        "rule_conflicts": r.rule_conflicts,
        "functional_conflicts": fc,
        "disjoint_conflicts": dc,
        "samples": [{"triple": ar.triple_line(t), "axiom": r.just[t]["axiom"],
                     "from": r.just[t]["from"]} for t in inferred[:20]],
    }
    with io.open(os.path.join(out, "report.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return {"ok": True, "report": report, "out_dir": out}


def ensure_closure(kb):
    """closure 比 nt 或公理文件旧就重跑一次。返回路径或 None。"""
    out = reason_dir(kb)
    clo = os.path.join(out, "closure.nt")
    src = nt_path(kb)
    if not os.path.exists(src):
        return None
    newest = max(os.path.getmtime(src), _mtime(axiom_path(kb)))
    if os.path.exists(clo) and os.path.getmtime(clo) >= newest:
        return clo
    res = run(kb)
    return os.path.join(out, "closure.nt") if res.get("ok") else None


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


# ───────────────────────── 作答 ─────────────────────────

_DISPLAY_ATTRS = ("productName", "partName", "deviceName", "customerName", "name", "label", "title")


def _display_map(triples):
    """{个体 IRI: 展示名}。优先中文名属性，退回本地名。"""
    names = {}
    for s, p, o in triples:
        if not ar.is_literal(o):
            continue
        name = p.rsplit("#", 1)[-1]
        if name in _DISPLAY_ATTRS and s not in names:
            names[s] = ar.lit_value(o)
    for s, _p, _o in triples:
        names.setdefault(s, s.rsplit("#", 1)[-1])
    return names


def answer(question, kb):
    """用公理集作答。命中返回结果字典，不命中返回 None（交回原链路）。

    只做一件事：问题里有实体 + 有某条公理的 ask 词 → 查 closure 里那条推导关系。
    不做句法分析，不猜意图。猜不出来就返回 None，绝不硬答。
    """
    q = (question or "").strip()
    if not q:
        return None
    doc = load(kb)
    chains = [c for c in (doc.get("chains") or []) if (c.get("ask") or {}).get("terms")]
    rules = [b for b in (doc.get("business_rules") or []) if (b.get("ask") or {}).get("terms")]
    if not chains and not rules:
        return None
    # 先看问题里有没有任何一条公理的中文问法，没有就直接退出 ——
    # 这样这个函数可以被放在问答链路前段而不拖慢普通问题（不必解析闭包图）。
    if not any(t in q for c in chains for t in c["ask"]["terms"]) and \
            not any(t in q for b in rules for t in b["ask"]["terms"]):
        return None

    clo = ensure_closure(kb)
    if not clo:
        return None
    triples, _bad = ar.parse_nt(clo)
    names = _display_map(triples)

    # 实体：问题里出现的个体（展示名 / 本地名 / 尾段编号如 P004），最长优先
    hits = []
    low = q.lower()
    for iri, disp in names.items():
        tail = iri.rsplit("#", 1)[-1]
        short = tail.rsplit("_", 1)[-1]
        for cand in (disp, tail, short):
            if cand and len(cand) >= 2 and cand.lower() in low:
                hits.append((len(cand), iri, disp))
    hits.sort(reverse=True)
    entity = hits[0][1] if hits else None

    def _fmt(iris):
        out = []
        for x in iris:
            tail = x.rsplit("#", 1)[-1]
            n = names.get(x) or tail
            if n == tail:                      # 没有中文名就用短号，别把整条 IRI 抛给用户
                n = tail.rsplit("_", 1)[-1] or tail
            if n not in out:
                out.append(n)
        return out

    # 属性链：正向/反向都试
    for ch in chains:
        terms = ch["ask"].get("terms") or []
        if not any(t in q for t in terms):
            continue
        prop = (doc.get("prefix") or "") + ch["implies"]
        if entity:
            fwd = [o for s, p, o in triples if s == entity and p == prop]
            if fwd:
                vals = _fmt(fwd)
                return {
                    "answer": "%s 的%s：%s（共 %d 项）"
                              % (names.get(entity, entity.rsplit("#", 1)[-1]),
                                 ch["ask"].get("label") or ch["implies"], "、".join(vals), len(vals)),
                    "evidence": [{"entity": names.get(entity), "attr": ch["implies"],
                                  "value": v, "source": "axiom", "score": 0.9,
                                  "axiom": ch.get("id")} for v in vals],
                    "structured": {"rule": "axiom_chain", "axiom": ch.get("id"),
                                   "direction": "forward"},
                    "engines": ["axiom"],
                }
            rev = [s for s, p, o in triples if p == prop and o == entity]
            if rev:
                vals = _fmt(rev)
                return {
                    "answer": "%s 关联到：%s（共 %d 项）"
                              % (names.get(entity, entity.rsplit("#", 1)[-1]),
                                 "、".join(vals), len(vals)),
                    "evidence": [{"entity": v, "attr": ch["implies"],
                                  "value": names.get(entity), "source": "axiom",
                                  "score": 0.85, "axiom": ch.get("id")} for v in vals],
                    "structured": {"rule": "axiom_chain", "axiom": ch.get("id"),
                                   "direction": "reverse"},
                    "engines": ["axiom"],
                }

    # 业务规则推出的结论：问题问的是结论类词（如"不合格/待处理"）
    for br in rules:
        terms = br["ask"].get("terms") or []
        if not any(t in q for t in terms):
            continue
        tprop = br["then"].get("prop")
        tval = str(br["then"].get("value"))
        prefix = doc.get("prefix") or ""
        hits2 = [s for s, p, o in triples
                 if p == prefix + tprop and ar.is_literal(o) and ar.lit_value(o) == tval]
        if not hits2:
            continue
        vals = _fmt(hits2)
        return {
            "answer": "按公理 %s，%s的有：%s（共 %d 个）"
                      % (br.get("id"), tval, "、".join(vals), len(vals)),
            "evidence": [{"entity": v, "attr": tprop, "value": tval,
                          "source": "axiom", "score": 0.9, "axiom": br.get("id")} for v in vals],
            "structured": {"rule": "axiom_rule", "axiom": br.get("id")},
            "engines": ["axiom"],
        }
    return None


def summary(kb):
    """给前端的状态摘要：有没有公理、多少条、上次推理什么时候。"""
    doc = load(kb)
    out = reason_dir(kb)
    rep_p = os.path.join(out, "report.json")
    rep = None
    if os.path.exists(rep_p):
        try:
            with io.open(rep_p, encoding="utf-8") as f:
                rep = json.load(f)
        except (OSError, ValueError):
            rep = None
    return {
        "kb": kb,
        "counts": {
            "chains": len(doc.get("chains") or []),
            "business_rules": len(doc.get("business_rules") or []),
            "functional": len(doc.get("functional") or []),
            "disjoint": len(doc.get("disjoint_with") or []),
            "inverse": len(doc.get("inverse_of") or []),
        },
        "updated_at": doc.get("updated_at") or "",
        "seed_report": doc.get("seed_report") or {},
        "last_run": rep,
        "closure": os.path.join(out, "closure.nt") if os.path.exists(os.path.join(out, "closure.nt")) else None,
    }
