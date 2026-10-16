#!/usr/bin/env python3
"""FK 写法兼容回归：`表.列` 与等值连接 `左表.左列=右表.右列` 都要能建图。

运行: cd codes && python -m pytest tests/test_fk_join_forms.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)

import pytest  # noqa: E402
import schema_ontology as so  # noqa: E402


DATA = {
    "seis_lines": [
        {"id": "L1", "name": "L1 测线", "region": "东部工区"},
        {"id": "L2", "name": "L2 测线", "region": "西部工区"},
    ],
    "seis_teams": [
        {"id": "T1", "name": "一队", "region": "东部工区"},
        {"id": "T2", "name": "二队", "region": "西部工区"},
    ],
}


def _entity(eid, label, table):
    return {
        "id": eid, "label": label, "table": table, "key": "id",
        "attributes": [
            {"name": "id", "label": "编号", "type": "string", "role": "identifier", "required": True},
            {"name": "name", "label": "名称", "type": "string"},
            {"name": "region", "label": "工区", "type": "string"},
        ],
    }


BASE = {
    "version": "1.0", "name": "fk-join-test", "industry": "seismic",
    "entities": [_entity("Seis_lines", "测线", "seis_lines"), _entity("Seis_teams", "班组", "seis_teams")],
    "constraints": [{"type": "unique", "on": "Seis_lines.id", "msg": "Seis_lines 主键 id 唯一"}],
}
SCHEMA_PLAIN = dict(BASE, relations=[
    {"id": "r_plain", "from": "Seis_lines", "to": "Seis_teams", "fk": "seis_lines.region",
     "cardinality": "N:1", "label": "所属班组（普通外键）"},
])
SCHEMA_EQ = dict(BASE, relations=[
    {"id": "r_eq", "from": "Seis_lines", "to": "Seis_teams", "fk": "seis_lines.region=seis_teams.region",
     "cardinality": "N:1", "label": "同工区班组（等值连接）"},
])


def test_plain_fk_builds():
    g = so.build_graph(DATA, SCHEMA_PLAIN)
    assert isinstance(g, dict)
    assert "edges" in g


def test_eq_join_fk_builds():
    # 当前代码在 `fk.split(".")` 处对等值连接抛 ValueError，本用例先红。
    g = so.build_graph(DATA, SCHEMA_EQ)
    assert isinstance(g, dict)
    assert "edges" in g


def test_eq_join_fk_matches_rows():
    g = so.build_graph(DATA, SCHEMA_EQ)
    labels = [e.get("label") for e in g["edges"]]
    assert "同工区班组（等值连接）" in labels


def test_ontology_model_builds():
    m = so.build_ontology_model(DATA, SCHEMA_EQ)
    assert isinstance(m, dict)


def test_to_nt_writes_file(tmp_path):
    out = str(tmp_path / "fk.nt")
    so.to_nt(DATA, SCHEMA_EQ, outpath=out)
    assert os.path.exists(out)
    with open(out, encoding="utf-8") as f:
        assert len(f.readlines()) > 0


def test_real_seismic_schema_if_present():
    schema_path = os.path.join(ROOT, "config", "ontology_schema_seismic.json")
    data_dir = os.path.join(ROOT, "data_seismic")
    if not (os.path.exists(schema_path) and os.path.isdir(data_dir)):
        pytest.skip("真实 seismic schema/数据不存在，跳过端到端回归")
    import json
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    data = {}
    for name in os.listdir(data_dir):
        if name.endswith(".json"):
            with open(os.path.join(data_dir, name), encoding="utf-8") as f:
                data[os.path.splitext(name)[0]] = json.load(f)
    g = so.build_graph(data, schema)
    assert isinstance(g, dict)