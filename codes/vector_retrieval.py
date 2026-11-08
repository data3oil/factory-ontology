#!/usr/bin/env python3
"""vector_retrieval.py — 本地向量语义检索（纯标准库，零第三方向量库依赖）

对知识图谱的每个实例实体做 embedding（本地 Ollama nomic-embed-text, 768维），
查询时把问题 embedding 后与所有实体向量做余弦相似度，召回 top-k 语义相近实体。

作用：作为"语义检索"层，补充 BM25(稀疏) 无法处理的语义模糊/同义/口语化查询
（如"最贵的产品"→price、"油轮有几艘"→船型）。本地 embedding，零 API 成本，
embedding 失败/服务不可用时回落空结果，绝不阻塞下游。

用法（被 api_server 集成）：
    from vector_retrieval import VectorIndex
    vx = VectorIndex.from_graph(graph, lexicon)   # graph=build_graph(nt) 返回的图
    hits = vx.search("最贵的产品", top_k=5)        # [{entity, score, text}]

独立测试：
    python vector_retrieval.py <nt文件> <lexicon.json> "<问题>"
"""
import os
import json
import sys
import time
import hashlib
import urllib.request
import urllib.error

# 从 model_config 读取向量模型配置（默认本地 nomic-embed-text）。读取失败回落默认，不阻塞。
try:
    from model_llm import get_embedding_config as _get_emb
    _emb = _get_emb()
except Exception:
    _emb = {}
OLLAMA_BASE = _emb.get("base_url") or os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
EMBED_MODEL = _emb.get("model") or os.environ.get("EMBED_MODEL", "nomic-embed-text")


def embed_text(text: str, base=OLLAMA_BASE, model=EMBED_MODEL):
    """调 Ollama /api/embeddings 生成向量。失败返回 None（不抛异常）。"""
    if not text or not text.strip():
        return None
    try:
        req = urllib.request.Request(
            base.rstrip("/") + "/api/embeddings",
            data=json.dumps({"model": model, "prompt": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        return data.get("embedding")
    except Exception:
        return None


def _cosine(a, b):
    """余弦相似度（纯标准库）。任一为空返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """本地向量语义检索索引：{实体: (文本, 向量)}。"""

    def __init__(self):
        self.entries = []      # [{entity, text, vector}]
        self._built = False
        # 🔴 降级可见化（上游问题：VectorIndex 静默空索引）。
        #    以前超时 / embedding 全失败都直接返回 `cls()`，调用方拿到一个
        #    entries=[] 的对象，**与「真的没有可索引实体」完全同形**。
        #    于是混合检索里 P2 悄悄变成全 0，看报告只会以为"语义检索没用"，
        #    实际上是构建超时——两种病因一种症状，没法排查。
        #
        #    这里只**记录**降级原因并告警，不改任何检索行为：
        #   - degraded: bool        是否走了降级
        #   - degrade_reason: str    人类可读原因（timeout / embed_failed / empty_graph）
        #   - degraded_at / n_candidates / n_embedded   现场数据
        #   调用方（run.py / benchmark / 混合检索）可据此判定，判不了也不影响原行为。
        self.degraded = False
        self.degrade_reason = ""
        self.degraded_at = 0
        self.n_candidates = 0
        self.n_embedded = 0
        self.from_cache = False

    def _mark_degraded(self, reason, n_candidates=0, n_embedded=0):
        """标记降级并 stderr 告警。告警走 stderr，不污染 stdout 的结构化输出。"""
        self.degraded = True
        self.degrade_reason = reason
        self.degraded_at = time.time()
        self.n_candidates = n_candidates
        self.n_embedded = n_embedded
        try:
            print("[vector_retrieval] ⚠️ 向量索引降级: %s（候选 %d，成功 %d）"
                  "——语义检索不可用，已回落 BM25" % (reason, n_candidates, n_embedded),
                  file=sys.stderr)
        except Exception:
            pass          # 告警本身绝不能把构建搞崩
        return self

    # ── 向量落盘缓存 ───────────────────────────────────────────
    # 语料不变就不该把同一个 KB 的 768 维向量重算一遍：服务一重启就重嵌，
    # 再撞上 5 秒预算，语义检索就永远是关着的。
    _CACHE_SUBDIR = os.path.join(".cache", "vector")

    @classmethod
    def _cache_path(cls, fingerprint):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), cls._CACHE_SUBDIR)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            return None
        return os.path.join(d, fingerprint + ".json")

    def _load_cache(self, path, model):
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, ValueError):
            return False
        if blob.get("model") != model:
            return False
        items = blob.get("items") or []
        if not items:
            return False
        self.entries = items
        self.n_embedded = len(items)
        self.n_candidates = int(blob.get("n_candidates") or len(items))
        self._built = True
        self.from_cache = True
        return True

    def _save_cache(self, path, model, n_candidates):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"model": model, "n_candidates": n_candidates,
                           "items": self.entries}, f)
            os.replace(tmp, path)
        except OSError:
            pass      # 缓存写不了不影响检索

    @classmethod
    def from_graph(cls, graph, lexicon=None, base=OLLAMA_BASE, model=EMBED_MODEL,
                   max_entries=2000, build_timeout=5.0, use_cache=True):
        """从 build_graph 返回的 {node: {rel: [vals]}} 建向量索引。
        每个实例实体 = 一条记录(名字 + 各属性[中文label]:值)，做 embedding。
        lexicon 提供 attr_en2cn 把英文字段名转中文 label，提升中文语义匹配。
        中文实体名（没有下划线）同样要进索引 —— 老代码用 `"_" not in name` 判断实例，
        把中文命名的实体整套跳过了。

        规模防护（防止万级实例逐条 embedding 卡死）：
        - max_entries：超过则按固定步长等距抽样（确定性），其余不进向量索引。
        - build_timeout：动态预算。先嵌一条量出单条耗时，再按 条数×耗时×1.5 放宽；
          仍超时则降级，并把"已嵌几条/共几条"写进降级原因。
        - use_cache：向量按 (文本指纹 + 模型) 落盘，重启复用。
        """
        vx = cls()
        en2cn = {}
        if lexicon:
            try:
                en2cn = lexicon.get("attr_en2cn", {}) or {}
            except AttributeError:
                pass
        # 先收集候选实例节点（文本已拼好），再决定抽样规模，避免构建前预嵌入
        candidates = []
        for node, props in graph.items():
            name = str(node).split("#")[-1] if "#" in str(node) else str(node).split("/")[-1]
            if name.startswith("__") or "domain" in name:
                continue
            # 实例实体的判定：带下划线 id（Valve_products_P001）或中文命名（空压机3号）。
            # 老代码只看下划线，中文名实体一个都进不了索引。
            if "_" not in name and not any("\u4e00" <= ch <= "\u9fff" for ch in name):
                continue
            if set(props) == {"label"} or list(props) == ["label"]:
                continue  # 类声明节点（仅 rdfs:label），非实例，跳过
            parts = [name]
            for rel, vals in props.items():
                cn = en2cn.get(rel, rel) if isinstance(en2cn, dict) else rel
                for v in (vals if isinstance(vals, list) else [vals]):
                    parts.append("%s:%s" % (cn, v))
            candidates.append((name, " ".join(parts)))
        # 规模上限：等距抽样（确定性）。random.sample 每次重启抽到的不是同一批，
        # 同一个问题两次运行召回集不同，排障时根本对不上账。
        if max_entries and max_entries > 0 and len(candidates) > max_entries:
            step = len(candidates) / float(max_entries)
            candidates = [candidates[int(i * step)] for i in range(max_entries)]

        n_candidates = len(candidates)
        fingerprint = hashlib.sha1(
            (model + "\n" + "\n".join(t for _n, t in candidates)).encode("utf-8")
        ).hexdigest()[:16]
        cache_path = cls._cache_path(fingerprint) if use_cache else None
        if cache_path and vx._load_cache(cache_path, model):
            return vx

        # 超时预算：先嵌一条量出单条耗时，再按规模放宽。
        # 固定 5 秒对 113 个实体都不够（实测只嵌进 1 条就降级），对 3 个实体又太宽。
        budget = float(build_timeout or 0)
        t0 = time.monotonic()
        for i, (name, text) in enumerate(candidates):
            vec = embed_text(text, base=base, model=model)
            if vec is None:
                continue
            vx.entries.append({"entity": name, "text": text, "vector": vec})
            if budget > 0 and i == 0:
                per_item = max(time.monotonic() - t0, 0.001)
                budget = max(budget, per_item * n_candidates * 1.5)
            if budget > 0 and time.monotonic() - t0 > budget:
                return vx._mark_degraded(
                    "构建超时（预算 %.1fs，已嵌入 %d/%d 条）"
                    % (budget, len(vx.entries), n_candidates),
                    n_candidates=n_candidates, n_embedded=len(vx.entries))
        vx._built = True
        # 候选全空与 embedding 全失败是两种病因，必须分开：
        # 前者是图本身没有可索引实体，后者是 Ollama 侧不可用。
        if n_candidates == 0:
            vx._mark_degraded("图里没有可索引的实例实体（候选 0 条）",
                              n_candidates=0, n_embedded=0)
        elif not vx.entries:
            vx._mark_degraded(
                "embedding 全部失败（%d 条候选全拿不到向量，多为 Ollama 不可用）"
                % n_candidates, n_candidates=n_candidates, n_embedded=0)
        else:
            vx.n_candidates = n_candidates
            vx.n_embedded = len(vx.entries)
            if cache_path:
                vx._save_cache(cache_path, model, n_candidates)
        return vx

    @classmethod
    def from_nt(cls, nt_file, lexicon=None, base=OLLAMA_BASE, model=EMBED_MODEL,
                max_entries=2000, build_timeout=5.0):
        """直接由 .nt 文件建向量索引（独立使用/测试入口）。"""
        import graph_rag as gr
        graph, _, _, _ = gr.build_graph(nt_file)
        return cls.from_graph(graph, lexicon=lexicon, base=base, model=model,
                              max_entries=max_entries, build_timeout=build_timeout)

    def search(self, query, top_k=5, min_score=0.0):
        """embedding 问题 → 余弦相似度召回 top-k 语义相近实体。
        返回 [{entity, score, text}] 降序。embedding 失败/无索引返回 []。"""
        if not self.entries:
            return []
        qv = embed_text(query)
        if qv is None:
            return []
        scored = [(_cosine(qv, e["vector"]), e) for e in self.entries]
        scored.sort(key=lambda x: -x[0])
        return [{"entity": e["entity"], "score": round(s, 4), "text": e["text"][:60]}
                for s, e in scored[:top_k] if s >= min_score]


def main():
    import sys as _sys
    if len(_sys.argv) < 4:
        print("用法: python vector_retrieval.py <nt文件> <lexicon.json> \"<问题>\" [top_k]")
        _sys.exit(1)
    nt_file, lex_file, question = _sys.argv[1], _sys.argv[2], _sys.argv[3]
    top_k = int(_sys.argv[4]) if len(_sys.argv) > 4 else 5
    lex = json.load(open(lex_file, encoding="utf-8")) if os.path.exists(lex_file) else None
    vx = VectorIndex.from_nt(nt_file, lexicon=lex)
    print("已建向量索引实体数:", len(vx.entries))
    for h in vx.search(question, top_k=top_k):
        print("  %.4f  %s  %s" % (h["score"], h["entity"], h["text"]))


if __name__ == "__main__":
    main()
