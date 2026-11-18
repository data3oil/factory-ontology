#!/usr/bin/env python3
"""类型维度建议与落库的回归用例（P2-9）。

验三件事：
  1. 建议器只挑"值域小、重复高、列名像类型维度"的列，不把主键/外键/自由文本当类型；
  2. 低置信候选（人员、供应商等更像实体引用的列）必须标 low，不能与高置信混为一谈；
  3. 人只把 approved 的写进 schema，且重复 apply 不产生重复条目；
  4. 落库后的维度能被导出器消费，生成类型类与枚举成员（TBox 侧可查）。

运行: cd codes && python -m pytest tests/test_type_suggestions.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))  # 仓库根，便于 import scripts/*

import pytest  # noqa: E402
import schema_ontology as so  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "scripts"))
import suggest_types as ST  # noqa: E402

DATA = {
    "tp_products": [
        {"id": "P%d" % i, "product_name": "产品%d" % i,
         "pressure_grade": ["PN16", "PN25", "PN40"][i % 3],
         "connection": ["法兰", "对夹"][i % 2],
         "checker": ["张工", "王工"][i % 2],
         "remark": "备注%d" % i}
        for i in range(1, 21)
    ],
    "tp_equipment": [
        {"id": "E%d" % i, "device_name": "设备%d" % i, "product_id": "P1",
         "status": ["运行中", "待机", "维护中"][i % 3]}
        for i in range(1, 10)
    ],
}

SCHEMA = {
    "version": "1.0", "name": "tp", "industry": "tp",
    "namespace": "https://example.com/tp#",
    "entities": [
        {"id": "Tp_products", "label": "产品", "table": "tp_products", "key": "id",
         "attributes": [{"name": c} for c in
                        ("id", "product_name", "pressure_grade", "connection", "checker", "remark")]},
        {"id": "Tp_equipment", "label": "设备", "table": "tp_equipment", "key": "id",
         "attributes": [{"name": c} for c in ("id", "device_name", "product_id", "status")]},
    ],
    "relations": [
        {"id": "eq_prod", "from": "Tp_equipment", "to": "Tp_products",
         "fk": "tp_equipment.product_id", "cardinality": "N:1", "label": "对应产品"},
    ],
}


def _cands():
    return ST.suggest(SCHEMA, DATA)


def test_picks_dimension_columns():
    got = {(c["entity"], c["column"]) for c in _cands() if c["kind"] == "dimension"}
    assert ("Tp_products", "pressure_grade") in got
    assert ("Tp_products", "connection") in got
    assert ("Tp_equipment", "status") in got


def test_skips_key_fk_and_free_text():
    got = {c["column"] for c in _cands() if c["kind"] == "dimension"}
    for bad in ("id", "product_id", "product_name", "remark"):
        assert bad not in got, "不该把 %s 当类型维度" % bad


def test_confidence_grading():
    d = {c["column"]: c for c in _cands() if c["kind"] == "dimension"}
    assert d["pressure_grade"]["confidence"] == "high"
    assert d["status"]["confidence"] == "high"
    # checker 值域小但更像实体引用 → 必须低置信
    assert d["checker"]["confidence"] == "low"


def test_candidates_carry_evidence():
    for c in _cands():
        assert c.get("evidence"), "候选必须有可核验的证据"
        assert c["approved"] is False, "默认必须未批准"


def test_apply_only_approved_and_idempotent(tmp_path):
    schema = json.loads(json.dumps(SCHEMA))
    sp = tmp_path / "schema.json"
    sp.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")

    cands = _cands()
    for c in cands:
        if c["kind"] == "dimension" and c["column"] in ("pressure_grade", "connection"):
            c["approved"] = True

    ST.apply_approved(str(sp), cands)
    d1 = json.loads(sp.read_text(encoding="utf-8"))
    dims = {(d["entity"], d["column"]) for d in d1["dimensions"]}
    assert dims == {("Tp_products", "pressure_grade"), ("Tp_products", "connection")}

    ST.apply_approved(str(sp), cands)  # 再跑一次
    d2 = json.loads(sp.read_text(encoding="utf-8"))
    assert len(d2["dimensions"]) == 2, "重复 apply 不应产生重复条目"
    assert {"entity": "Tp_products", "column": "status"} not in d2["dimensions"]


def test_unapproved_writes_nothing(tmp_path):
    sp = tmp_path / "schema.json"
    sp.write_text(json.dumps(SCHEMA, ensure_ascii=False), encoding="utf-8")
    ST.apply_approved(str(sp), _cands())  # 全部未批准
    d = json.loads(sp.read_text(encoding="utf-8"))
    assert "dimensions" not in d


def test_dimension_classes_exported():
    schema = json.loads(json.dumps(SCHEMA))
    schema["dimensions"] = [
        {"entity": "Tp_products", "column": "pressure_grade", "label": "产品-压力等级",
         "values": ["PN16", "PN25", "PN40"]},
    ]
    txt = "\n".join(so.to_nt(DATA, schema))
    assert "Tp_products_pressure_gradeType>" in txt
    for v in ("PN16", "PN25", "PN40"):
        assert "Tp_products_pressure_gradeType_%s" % v in txt
    # 类层次：成员 subClassOf 类型类
    assert ("_PN16> <http://www.w3.org/2000/01/rdf-schema#subClassOf> "
            "<https://example.com/tp#Tp_products_pressure_gradeType> .") in txt


def test_dimension_does_not_touch_instance_attributes():
    """只增 TBox，不改实例属性：实例上的 pressure_grade 仍是字面量。"""
    schema = json.loads(json.dumps(SCHEMA))
    schema["dimensions"] = [{"entity": "Tp_products", "column": "pressure_grade",
                             "label": "产品-压力等级", "values": ["PN16"]}]
    txt = "\n".join(so.to_nt(DATA, schema))
    assert "Tp_products_P3> <https://example.com/tp#pressureGrade> \"PN16\"^^" in txt
