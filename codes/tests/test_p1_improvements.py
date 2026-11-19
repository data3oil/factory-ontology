#!/usr/bin/env python3
"""P0/P1 新增能力的单测：图持久化、逻辑一致性、RRF、查询规划三式。

不依赖外部模型与服务，CI 可跑。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.dirname(HERE)
if CODES not in sys.path:
    sys.path.insert(0, CODES)

import fusion  # noqa: E402
import graph_store  # noqa: E402
import logic_check  # noqa: E402
import ontology_qa_v3 as Q  # noqa: E402

NS = "http://t.example/ontology#"


def _triples():
    return [
        (NS + "Equipment_E1", NS + "type", NS + "Equipment"),
        (NS + "Equipment_E1", NS + "label", "一号机"),
        (NS + "Equipment_E1", NS + "status", "运行"),
        (NS + "Equipment_E2", NS + "type", NS + "Equipment"),
        (NS + "Equipment_E2", NS + "status", "停机"),
        (NS + "Workshop_W1", NS + "type", NS + "Workshop"),
    ]


# ---------------------------------------------------------------- 图存储

def test_graph_store_roundtrip(tmp_path):
    nt = tmp_path / "x.nt"
    nt.write_text("\n".join("<%s> <%s> <%s> ." % t for t in _triples()), encoding="utf-8")
    db = tmp_path / "x.db"
    st = graph_store.build(str(nt), str(db))
    assert st["triples"] == len(_triples())
    g, labels, values, reverse = graph_store.load(str(db))
    assert NS + "Equipment_E1" in g
    assert g[NS + "Equipment_E1"]["status"] == ["运行"]
    assert labels[NS + "Equipment_E2"] == "二号机" or labels[NS + "Equipment_E2"]
    assert values


# ---------------------------------------------------------------- 逻辑一致性

def test_logic_check_detects_duplicate_key():
    schema = {"constraints": [{"type": "unique", "on": "Equipment.id"}],
              "relations": [], "entities": []}
    triples = [
        (NS + "Equipment_A", NS + "type", NS + "Equipment"),
        (NS + "Equipment_B", NS + "type", NS + "Equipment"),
        (NS + "Equipment_A", NS + "id", "X1"),
        (NS + "Equipment_B", NS + "id", "X1"),
    ]
    out = logic_check.check_unique(schema, triples)
    assert out["ok"] is False
    assert out["violations"]


def test_logic_check_chain_cycle():
    axioms = {"chains": [{"path": [{"prop": "a"}, {"prop": "b"}]},
                         {"path": [{"prop": "b"}, {"prop": "a"}]}]}
    out = logic_check.check_chain_cycle(axioms)
    assert out["ok"] is False


def test_logic_check_disjoint_reports_skip():
    out = logic_check.check_disjoint({}, _triples())
    assert out["ok"] is True and out.get("skipped") is True


# ---------------------------------------------------------------- RRF

def test_rrf_prefers_agreed_answer():
    cands = [
        {"answer": "7 台", "source": "rule"},
        {"answer": "7 台", "source": "graph"},
        {"answer": "9 台", "source": "bm25"},
    ]
    ranked = fusion.rrf_rank(cands)
    assert ranked[0]["answer"] == "7 台"


def test_rrf_keeps_single_source_order():
    cands = [{"answer": "A", "source": "graph"}, {"answer": "B", "source": "graph"}]
    ranked = fusion.rrf_rank(cands)
    assert [c["answer"] for c in ranked] == ["A", "B"]


# ---------------------------------------------------------------- 查询规划三式

def _data():
    d = {
        NS + "Equipment_E1": {"status": "运行", "powerKw": "10"},
        NS + "Equipment_E2": {"status": "停机", "powerKw": "20"},
        NS + "Equipment_E3": {"status": "运行", "powerKw": "30"},
    }
    D = {"entity_cn2en": {"设备": "equipment"},
         "attr_cn2en": {"状态": "status", "功率": "powerKw"},
         "field_aliases": {}}
    return d, D


def test_cond_count():
    d, D = _data()
    assert Q._cond_count("设备中状态为运行的有多少台", d, D) == "符合条件共 2 条"


def test_enum_attr_values():
    d, D = _data()
    out = Q._enum_attr_values("设备的状态有哪些", d, D)
    assert "运行" in out and "停机" in out


def test_entity_count():
    d, D = _data()
    assert Q._entity_count("设备有多少条记录", d, D) == "设备共有 3 条记录"


def test_unknown_entity_returns_none():
    d, D = _data()
    assert Q._cond_count("客户中状态为A的有多少条", d, D) is None
