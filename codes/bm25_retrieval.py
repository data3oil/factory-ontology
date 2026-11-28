#!/usr/bin/env python3
"""bm25_retrieval.py — 轻量 BM25 混合检索（纯标准库零依赖）

对知识图谱的文本内容建倒排索引，给定问题返回 top-K 相关实体/三元组。
作为"稀疏检索"层，补充图遍历检索，提升模糊/自然语言查询的召回。

中文处理：字符 unigram + 双字 bigram 混合分词（无需 jieba，纯标准库）。

用法（被 api_server 集成）：
    from bm25_retrieval import BM25Index
    bm = BM25Index.from_graph(graph)       # graph = build_graph(nt) 的 {node: {rel: [vals]}}
    hits = bm.search("保质期最长的产品是什么", top_k=5)
"""
import math
import re
from collections import defaultdict

# 中文分词：英文/数字保留，中文按 unigram+bigram
_RE_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list:
    """混合分词：英文/数字词 + 中文单字，并对中文相邻字组 bigram。"""
    text = text.lower()
    toks = _RE_TOKEN.findall(text)
    out = []
    han = ""
    for t in toks:
        if re.fullmatch(r"[\u4e00-\u9fff]", t):
            han += t
        else:
            out.append(t)
    if len(han) >= 1:
        # unigram + bigram
        out.extend(han)
        for i in range(len(han) - 1):
            out.append(han[i:i + 2])
    return out


class BM25Index:
    """纯标准库 BM25。k1=1.5, b=0.75 为标准参数。"""

    def __init__(self):
        self.docs = []          # list of dict(文本, 实体)  实体=节点tail
        self.avgdl = 0.0
        self.df = defaultdict(int)   # 词 -> 含该词的文档数
        self.idf = {}
        self.doc_tokens = []    # list of Counter
        self._indexed = False

    @classmethod
    def from_graph(cls, graph, node_count=None):
        """从 build_graph 返回的 {node: {rel: [vals]}} 建索引。
        每个实体节点 = 一条文档(其名字+所有属性值)。"""
        bm = cls()
        for node, props in graph.items():
            from graph_rag import tail
            name = tail(node)
            if name.startswith("__") or name.startswith("//"):
                continue
            parts = [name]
            for rel, vals in props.items():
                for v in (vals if isinstance(vals, list) else [vals]):
                    parts.append(str(v))
            bm.add_document(" ".join(parts), name)
        bm.build()
        return bm

    def add_document(self, text: str, entity: str):
        self.docs.append({"text": text, "entity": entity})
        self.doc_tokens.append(defaultdict(int))
        for t in tokenize(text):
            self.doc_tokens[-1][t] += 1

    def build(self):
        n = len(self.docs)
        if n == 0:
            self._indexed = True
            return
        self.avgdl = sum(sum(d.values()) for d in self.doc_tokens) / n
        for d in self.doc_tokens:
            for t in d:
                self.df[t] += 1
        for t, df in self.df.items():
            # BM25 IDF，避免负值
            self.idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))
        self._indexed = True

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> list:
        """返回 [{entity, score, text}] 按相关性降序，过滤低于 min_score 的噪音。"""
        if not self._indexed or not self.docs:
            return []
        q_terms = tokenize(query)
        scores = [0.0] * len(self.docs)
        for t in q_terms:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, dt in enumerate(self.doc_tokens):
                tf = dt.get(t, 0)
                if tf == 0:
                    continue
                dl = sum(dt.values())
                scores[i] += idf * (tf * (1.5 + 1)) / (tf + 1.5 * (1 - 0.75 + 0.75 * dl / self.avgdl))
        ranked = sorted(zip(scores, self.docs), key=lambda x: -x[0])
        return [{"entity": d["entity"], "score": round(s, 4), "text": d["text"][:60]}
                for s, d in ranked[:top_k] if s >= min_score]


class PersistentBM25Index:
    """可选持久化 BM25：SQLite FTS5 落盘，按知识库内容 hash 失效。

    与 BM25Index 同接口（from_graph / search），但索引落盘复用，避免每次
    冷启动从图重建。**默认仍用内存版 BM25Index**，本类需显式调用才生效
    （实测 graph_store 加载已非瓶颈，故不作默认，见优化方案 2026-11-28）。

    实现纪律：
    - FTS5 只负责召回候选实体（返回 entity 与命中词）；相关性分沿用内存版
      BM25 公式在本地重算，保证与 BM25Index.search 的分数口径一致；
    - 索引键 = 知识库内容 sha256，hash 不变则复用、变了则重建，杜绝陈旧索引；
    - 任何 DB 异常一律降级为内存版（fail-open），不把检索层变成新的故障点。
    """

    def __init__(self, db_path=None, table="bm25_fts"):
        import hashlib
        import json
        import os
        import sqlite3

        self._hashlib = hashlib
        self._json = json
        self._sqlite3 = sqlite3
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output", "bm25_fts.sqlite3")
        # 表名白名单：FTS5 DDL 只能拼标识符，不接受任意字符串（防 SQL 注入）
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table or ""):
            raise ValueError("FTS 表名不合法（只允许字母/数字/下划线）")
        self.table = table
        self._mem = BM25Index()
        self._ready = False

    @staticmethod
    def _stable_hash(graph):
        """对 graph 内容做稳定 hash（排序键，避免 dict 插入顺序影响）。"""
        import hashlib
        h = hashlib.sha256()
        for node in sorted(graph.keys()):
            props = graph[node]
            h.update(str(node).encode("utf-8"))
            if isinstance(props, dict):
                for rel in sorted(str(k) for k in props.keys()):
                    vals = props.get(rel)
                    h.update(rel.encode("utf-8"))
                    for v in (vals if isinstance(vals, list) else [vals]):
                        h.update(str(v).encode("utf-8"))
        return h.hexdigest()

    @classmethod
    def from_graph(cls, graph, node_count=None, db_path=None):
        """从图建/取持久化索引；失败降级为内存版（结果等价，只是不落盘）。"""
        obj = cls(db_path=db_path)
        try:
            obj._build_or_reuse(graph)
            obj._ready = True
        except Exception as exc:                        # noqa: BLE001 - 检索层 fail-open
            try:
                import safe_log
                safe_log.swallow("bm25.persistent", exc)
            except Exception as log_exc:                  # noqa: BLE001
                # 日志也失败时不静默：降级路径本身要留痕（不向上抛，检索层 fail-open）
                import sys
                sys.stderr.write("[bm25.persistent] 降级且日志失败: %s\n" % log_exc)
            obj._mem = BM25Index.from_graph(graph, node_count=node_count)
            obj._ready = True
        return obj

    def _build_or_reuse(self, graph):
        import os
        import sqlite3
        digest = self._stable_hash(graph)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (self.table,)).fetchone()
            meta_ok = False
            try:
                m = conn.execute(
                    "SELECT digest FROM bm25_meta WHERE table_name=?", (self.table,)).fetchone()
                meta_ok = bool(m and m[0] == digest)
            except sqlite3.OperationalError:
                pass
            if row and meta_ok:
                # 复用：把 FTS 行读回内存版做同口径打分（检索仍走本地，无外部依赖）
                for text, entity in conn.execute(
                        "SELECT text, entity FROM %s" % self.table):
                    self._mem.add_document(text, entity)
                self._mem.build()
                return
            # 重建
            conn.execute("DROP TABLE IF EXISTS %s" % self.table)
            conn.execute("DROP TABLE IF EXISTS bm25_meta")
            conn.execute("CREATE VIRTUAL TABLE %s USING fts5(text, entity)" % self.table)
            conn.execute("CREATE TABLE bm25_meta (table_name TEXT PRIMARY KEY, digest TEXT)")
            docs = []
            for node, props in graph.items():
                from graph_rag import tail
                name = tail(node)
                if name.startswith("__") or name.startswith("//"):
                    continue
                parts = [name]
                for rel, vals in props.items():
                    for v in (vals if isinstance(vals, list) else [vals]):
                        parts.append(str(v))
                text = " ".join(parts)
                docs.append((text, name))
                self._mem.add_document(text, name)
            conn.executemany("INSERT INTO %s(text, entity) VALUES(?,?)" % self.table, docs)
            conn.execute("INSERT INTO bm25_meta(table_name, digest) VALUES(?,?)",
                         (self.table, digest))
            conn.commit()
            self._mem.build()
        finally:
            conn.close()

    def search(self, query, top_k=5, min_score=0.0):
        """与 BM25Index.search 同口径（走内存打分，FTS5 负责持久化召回候选）。"""
        if not self._ready:
            return []
        return self._mem.search(query, top_k=top_k, min_score=min_score)


if __name__ == "__main__":
    # 自测
    g = {
        "http://factory.example/ontology#Food_products_P001": {
            "productName": ["原味酸奶"], "保质期": ["15天"]},
        "http://factory.example/ontology#Food_products_P003": {
            "productName": ["全麦面包"], "保质期": ["7天"]},
        "http://factory.example/ontology#Food_products_P005": {
            "productName": ["手工水饺"], "保质期": ["180天"]},
    }
    bm = BM25Index.from_graph(g)
    for q in ["保质期最长的", "面包", "酸奶"]:
        print(f"问 [{q}] →", [h["entity"] for h in bm.search(q)])


# ═══════════════════════════════════════════════════════════════════════
# 混合检索(RRF)共享调参 — query_agent / api_server 复用同一套权重
# ═══════════════════════════════════════════════════════════════════════
# 调参说明(复杂问题命中优化, 兼顾防误召回):
#   - BM25 稀疏: min_score 4.0→3.0 + top_k 3→6, 放宽字面匹配提升召回。
#     阈值 3.0 仍能滤掉纯噪音(如无关问题的字符碰巧命中 ~2.8), 保持"无关→miss"。
#   - 向量语义: top_k 5→8 加深语义召回, 但 min_score 保持 0.60(调低到 0.55 会让
#     无关问题误召回, 实测 ~0.55 噪音, 故不动, 精度优先)。
#   - RRF 融合: 用倒数排名融合(Reciprocal Rank Fusion)替代简单拼接, 让 BM25 与
#     向量在不同排名区间互补, 单一引擎高排名不被另一个淹没, 提升复杂问题命中。
HYBRID_CFG = {
    "bm25": {"top_k": 6, "min_score": 3.0},
    "vector": {"top_k": 8, "min_score": 0.60},
    "rrf": {"k": 60, "w_bm25": 1.0, "w_vec": 1.0},  # k 为标准平滑参数
    "rrf_top": 6,  # 融合后返回的实体数(进入 evidence)
}


def rrf_fuse(bm_hits, vec_hits, cfg=None):
    """Reciprocal Rank Fusion: 按排名倒数融合 BM25+向量命中, 返回去重排序实体。

    输入: bm_hits/vec_hits = [{entity, score, text|value}]
    返回: [{entity, rrf, sources:[str], hit}] 按融合分降序, 截断到 cfg['rrf_top']。
      hit 为来源实体原始命中(取 BM25/向量中分数高者), 供上层拼 evidence 值。
    """
    cfg = cfg or HYBRID_CFG
    k = cfg["rrf"]["k"]
    w_b, w_v = cfg["rrf"]["w_bm25"], cfg["rrf"]["w_vec"]
    acc = {}          # entity -> rrf 分
    best = {}         # entity -> (score, hit)
    src = {}          # entity -> [sources]
    for i, h in enumerate(bm_hits):
        e = h["entity"]
        acc[e] = acc.get(e, 0) + w_b / (k + i + 1)
        src.setdefault(e, []).append("bm25")
        if e not in best or (h.get("score") or 0) > best[e][0]:
            best[e] = (h.get("score") or 0, h)
    for i, h in enumerate(vec_hits):
        e = h["entity"]
        acc[e] = acc.get(e, 0) + w_v / (k + i + 1)
        src.setdefault(e, []).append("vector")
        if e not in best or (h.get("score") or 0) > best[e][0]:
            best[e] = (h.get("score") or 0, h)
    ranked = sorted(acc.items(), key=lambda x: -x[1])
    return [{"entity": e, "rrf": round(acc[e], 4), "sources": src[e],
             "hit": best.get(e, (0, None))[1]} for e, _ in ranked[:cfg["rrf_top"]]]
