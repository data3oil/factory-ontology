# -*- coding: utf-8 -*-
"""证据层（evidence_kb）：给工厂本体补"可逐字核对的原文出处"。

只做加法：不动本体、图谱与既有 RAG（bm25_retrieval / 向量 / RRF）。
入口：
  python -m evidence_kb.indexer --corpus ../docs --db data/evidence/default.db --full
  GET /api/evidence/search?q=...
  GET /api/evidence/verdict?q=...
"""
from .indexer import build, segment, bigrams                     # noqa: F401
from .retrieval import search, judge, status, terms_of            # noqa: F401

__all__ = ["build", "segment", "bigrams", "search", "judge", "status", "terms_of"]
