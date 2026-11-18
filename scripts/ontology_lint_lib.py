#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ontology_lint_lib.py — 本体导出件静态检查的共用解析（纯标准库，零第三方依赖）。

只做一件事：把 ontology_export.py 产出的 Turtle/N-Triples 解析成可判定的三元组，
供 verify_iri_integrity / verify_oops / verify_shacl 复用，避免每个门各写一套解析。

支持两种真实格式（都是本仓库自己产出的）：
  1. 标准多行 Turtle（ontology.ttl / shapes.ttl）：`s p o1 , o2 ; p2 o3 .`
  2. 缩略 N-Triples（ontology-full.ttl）：每行一条 `s <p> o .`

解析结果统一展开成完整 IRI；字面量保留原文并打 is_lit 标记；空白节点打 is_blank。
不做推理、不做校验，只负责"读得准"。
"""
import re

PREFIX_RE = re.compile(r'@prefix\s+([A-Za-z_][\w\-]*|)\s*:\s*<([^>]*)>\s*\.')
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
OWL_OBJPROP = "http://www.w3.org/2002/07/owl#ObjectProperty"
OWL_DATAPROP = "http://www.w3.org/2002/07/owl#DatatypeProperty"
RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"

# Turtle 局部名的合法字符（保守子集：ASCII 字母数字与 _ - . 且不以 .= 结尾）
_SAFE_LOCAL = re.compile(r'^[A-Za-z_][A-Za-z0-9_\-]*$')


class Prefixes:
    def __init__(self):
        self.p = {}

    def expand(self, tok):
        """token → 完整 IRI；无法展开时原样返回。"""
        tok = tok.strip()
        if not tok:
            return tok
        if tok.startswith("<") and tok.endswith(">"):
            return tok[1:-1].strip()
        if tok.startswith("_:"):
            return tok
        if ":" in tok:
            pre, _, local = tok.partition(":")
            if pre in self.p:
                return self.p[pre] + local
        elif "" in self.p:  # 默认前缀写法 `:x`
            return self.p[""] + tok
        return tok


def _tokens(s):
    """把一段 Turtle 语句片段切成 token（正确处理字面量、IRI、括号组）。"""
    toks, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in '";':
            if c == '"':
                j = i + 1
                while j < n:
                    if s[j] == "\\":
                        j += 2
                        continue
                    if s[j] == '"':
                        j += 1
                        break
                    j += 1
                k = j
                if k < n and s[k] == "@":
                    k += 1
                    while k < n and (s[k].isalnum() or s[k] == "-"):
                        k += 1
                elif s.startswith("^^", k):
                    k += 2
                    while k < n and not s[k].isspace() and s[k] not in ";,.":
                        k += 1
                toks.append(s[i:k])
                i = k
                continue
            i += 1
            continue
        if c == "<":
            j = s.find(">", i)
            j = n - 1 if j < 0 else j
            toks.append(s[i:j + 1])
            i = j + 1
            continue
        if c in "([":
            depth, j = 0, i
            while j < n:
                if s[j] in "([":
                    depth += 1
                elif s[j] in ")]":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            toks.append(s[i:j])
            i = j
            continue
        j = i
        while j < n and not s[j].isspace() and s[j] not in ";,":
            j += 1
        toks.append(s[i:j])
        i = j
    return toks


def _classify(tok, pf):
    """token → (value, kind)。kind ∈ {iri, lit, blank, group}。"""
    if tok.startswith('"'):
        return tok, "lit"
    if tok.startswith("_:"):
        return tok, "blank"
    if tok.startswith("[") or tok.startswith("("):
        return tok, "group"
    return pf.expand(tok), "iri"


def parse_turtle(text):
    """解析 Turtle 文本 → (prefixes, triples)。

    triples: list[(s, p, o, s_kind, o_kind)]，s/p 展开为完整 IRI，o 按 kind 处理。
    """
    pf = Prefixes()
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = PREFIX_RE.match(s)
        if m:
            pf.p[m.group(1)] = m.group(2)
            continue
        lines.append(raw.rstrip())

    stmts, buf = [], []
    for ln in lines:
        buf.append(ln)
        if re.search(r"\s\.\s*$", ln) or ln.rstrip() == ".":
            stmts.append(" ".join(buf))
            buf = []
    if buf:
        stmts.append(" ".join(buf))

    triples = []
    for st in stmts:
        st = st.strip()
        if st.endswith("."):
            st = st[:-1]
        blocks = _split_top(st, ";")
        if not blocks:
            continue
        head = _tokens(blocks[0])
        if len(head) < 2:
            continue
        s_tok, s_val, s_kind = head[0], _classify(head[0], pf)[0], _classify(head[0], pf)[1]
        # 第一个块：pred + objects
        p_tok = head[1]
        p_val = RDF_TYPE if p_tok == "a" else pf.expand(p_tok)
        objs = head[2:]
        for o in objs:
            ov, ok = _classify(o, pf)
            triples.append((s_val, p_val, ov, s_kind, ok))
        # 后续块：pred + objects
        for blk in blocks[1:]:
            tk = _tokens(blk)
            if len(tk) < 2:
                continue
            p_tok = tk[0]
            p_val = RDF_TYPE if p_tok == "a" else pf.expand(p_tok)
            for o in tk[1:]:
                ov, ok = _classify(o, pf)
                triples.append((s_val, p_val, ov, s_kind, ok))
    return pf, triples


def _split_top(s, sep):
    """按顶层分隔符切（忽略引号内、括号内）。"""
    out, depth, cur, in_str, esc = [], 0, [], False, False
    for ch in s:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == "\\":
            cur.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            cur.append(ch)
            continue
        if not in_str:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == sep and depth == 0:
                out.append("".join(cur))
                cur = []
                continue
        cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def local_name(iri):
    """取 IRI 的局部名（# 或最后一个 / 之后）。"""
    if "#" in iri:
        return iri.rsplit("#", 1)[1]
    return iri.rstrip("/").rsplit("/", 1)[-1]


def is_safe_local(iri):
    """局部名是否落在 ASCII 安全子集内（中文局部名会导致 Turtle 不可移植）。"""
    return bool(_SAFE_LOCAL.match(local_name(iri)))


def subject_iris(triples):
    return {s for s, _p, _o, _sk, _ok in triples}


def object_iris(triples):
    return {o for _s, _p, o, _sk, ok in triples if ok == "iri"}


def classes(triples):
    """被声明为 owl:Class 的 IRI 集合。"""
    return {s for s, p, o, _sk, ok in triples if p == RDF_TYPE and ok == "iri" and o == OWL_CLASS}


def load(path):
    with open(path, encoding="utf-8") as f:
        return parse_turtle(f.read())
