# -*- coding: utf-8 -*-
"""test_vector_index.py — 向量索引的可用性约定（不联网，全部替换 embedding）。

守三条曾经踩过的坑：
1. 中文实体名不能因为"没有下划线"就被当成类声明跳过；
2. 抽样必须确定：同一个库两次构建召回集必须一样（老代码 random.sample）；
3. 向量要能落盘复用：语料不变时第二次构建不该再调 embedding。
"""
import os
import sys

_CODES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODES_DIR not in sys.path:
    sys.path.insert(0, _CODES_DIR)

import vector_retrieval as vr  # noqa: E402


def _graph(n=30):
    g = {}
    for i in range(n):
        g["Thing_item_%03d" % i] = {"name": ["物料%d" % i], "price": [str(i)]}
    g["空压机3号"] = {"name": ["空压机3号"], "status": ["running"]}
    g["Thing_item"] = {"label": ["物料"]}          # 类声明节点，必须跳过
    return g


def _fake_embed(text, base=None, model=None):
    # 稳定伪向量：同一个文本永远同一个方向，且不同文本可区分
    h = sum(ord(c) * (i + 1) for i, c in enumerate(text))
    return [((h + k) % 97) / 97.0 for k in range(8)]


def _no_network(monkeypatch):
    monkeypatch.setattr(vr, "embed_text", _fake_embed)


def test_chinese_entity_names_are_indexed(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(vr.VectorIndex, "_CACHE_SUBDIR", str(tmp_path / "cache"))
    vx = vr.VectorIndex.from_graph(_graph(), use_cache=False)
    names = {e["entity"] for e in vx.entries}
    assert "空压机3号" in names, "中文命名实体必须进索引"
    assert "Thing_item" not in names, "类声明节点不该进索引"


def test_sampling_is_deterministic(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(vr.VectorIndex, "_CACHE_SUBDIR", str(tmp_path / "cache"))
    a = vr.VectorIndex.from_graph(_graph(), max_entries=10, use_cache=False)
    b = vr.VectorIndex.from_graph(_graph(), max_entries=10, use_cache=False)
    assert [e["entity"] for e in a.entries] == [e["entity"] for e in b.entries]
    assert len(a.entries) == 10


def test_cache_is_reused(tmp_path, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setattr(vr.VectorIndex, "_CACHE_SUBDIR", str(tmp_path / "cache"))
    calls = {"n": 0}

    def counting(text, base=None, model=None):
        calls["n"] += 1
        return _fake_embed(text)

    monkeypatch.setattr(vr, "embed_text", counting)
    first = vr.VectorIndex.from_graph(_graph(), use_cache=True)
    assert calls["n"] > 0 and not first.from_cache

    calls["n"] = 0
    second = vr.VectorIndex.from_graph(_graph(), use_cache=True)
    assert calls["n"] == 0, "语料未变时不该再调 embedding"
    assert second.from_cache is True
    assert [e["entity"] for e in second.entries] == [e["entity"] for e in first.entries]
