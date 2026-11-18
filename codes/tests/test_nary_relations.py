#!/usr/bin/env python3
"""N 元关系（关联表）建本用例 —— 先定语义，再验实现。

这套用例是 P0-3 的验收基线：改 `_infer_relations` / `_nt_instances` 之前先看它。

口径：
  · 二元关联表（2 个外键 + 无自身列）→ 可折叠为两条直连边，语义不变；
  · 三元及以上 → 必须保留关联实体，折叠会把"这一组参与方共同构成一次关联"
    拆成互不相干的二元边，属于语义改写，禁止；
  · 无独立主键的连接表 → IRI 用参与者组合键，不得依赖行序号（否则增删行就漂移）。

运行: cd codes && python -m pytest tests/test_nary_relations.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)

import pytest  # noqa: E402
import schema_ontology as so  # noqa: E402

NS = "https://ontology.example.com/industry/nary#"


def _ent(eid, label, table, key, attrs, detail=False):
    e = {"id": eid, "label": label, "table": table, "key": key,
         "attributes": [{"name": a, "label": a, "type": "string"} for a in attrs]}
    if detail:
        e["detail"] = True
    return e


# ── 数据：批次 / 原料 / 设备 / 批次配料（二元）/ 批次工序（三元）────────────────
DATA = {
    "nary_batches": [
        {"id": "B1", "name": "一号批次"},
        {"id": "B2", "name": "二号批次"},
    ],
    "nary_raws": [
        {"id": "R1", "name": "钢材"},
        {"id": "R2", "name": "铜材"},
    ],
    "nary_equips": [
        {"id": "E1", "name": "熔炉"},
        {"id": "E2", "name": "冲压机"},
    ],
    # 二元连接表：只有两个外键
    "nary_ingredient": [
        {"batch_id": "B1", "raw_id": "R1"},
        {"batch_id": "B1", "raw_id": "R2"},
    ],
    # 三元连接表：三个外键，表本身没有任何自身列
    "nary_operation": [
        {"batch_id": "B1", "raw_id": "R1", "equip_id": "E1"},
        {"batch_id": "B1", "raw_id": "R2", "equip_id": "E2"},
        {"batch_id": "B2", "raw_id": "R1", "equip_id": "E1"},
    ],
    # 带自身列的连接表（销售记录）：2 个外键 + 数量/金额
    "nary_sales": [
        {"id": "S1", "batch_id": "B1", "equip_id": "E1", "qty": "10", "amount": "100"},
    ],
}

ENTITIES = [
    _ent("Nary_batches", "批次", "nary_batches", "id", ["id", "name"]),
    _ent("Nary_raws", "原料", "nary_raws", "id", ["id", "name"]),
    _ent("Nary_equips", "设备", "nary_equips", "id", ["id", "name"]),
    _ent("Nary_ingredient", "批次配料", "nary_ingredient", "batch_id", ["batch_id", "raw_id"], detail=True),
    _ent("Nary_operation", "批次工序", "nary_operation", "batch_id",
         ["batch_id", "raw_id", "equip_id"], detail=True),
    _ent("Nary_sales", "销售记录", "nary_sales", "id",
         ["id", "batch_id", "equip_id", "qty", "amount"]),
]

RELATIONS = [
    {"id": "ing_batch", "from": "Nary_ingredient", "to": "Nary_batches",
     "fk": "nary_ingredient.batch_id", "cardinality": "N:1", "label": "所属批次"},
    {"id": "ing_raw", "from": "Nary_ingredient", "to": "Nary_raws",
     "fk": "nary_ingredient.raw_id", "cardinality": "N:1", "label": "使用原料"},
    {"id": "op_batch", "from": "Nary_operation", "to": "Nary_batches",
     "fk": "nary_operation.batch_id", "cardinality": "N:1", "label": "所属批次"},
    {"id": "op_raw", "from": "Nary_operation", "to": "Nary_raws",
     "fk": "nary_operation.raw_id", "cardinality": "N:1", "label": "使用原料"},
    {"id": "op_equip", "from": "Nary_operation", "to": "Nary_equips",
     "fk": "nary_operation.equip_id", "cardinality": "N:1", "label": "加工设备"},
    {"id": "sale_batch", "from": "Nary_sales", "to": "Nary_batches",
     "fk": "nary_sales.batch_id", "cardinality": "N:1", "label": "所属批次"},
    {"id": "sale_equip", "from": "Nary_sales", "to": "Nary_equips",
     "fk": "nary_sales.equip_id", "cardinality": "N:1", "label": "生产设备"},
]

SCHEMA = {
    "version": "1.0", "name": "nary-test", "industry": "nary",
    "namespace": NS, "entities": ENTITIES, "relations": RELATIONS,
}

ENTS = {e["id"]: e for e in ENTITIES}


# ── 1. 结构判定 ────────────────────────────────────────────────────────────────
def test_binary_join_is_foldable():
    m = so.association_meta(ENTS["Nary_ingredient"], RELATIONS, DATA)
    assert m["is_association"] and m["arity"] == 2
    assert m["foldable"] is True
    assert m["key_cols"] == ["batch_id", "raw_id"]  # 主键是外键列 → 用组合键


def test_ternary_join_is_not_foldable():
    m = so.association_meta(ENTS["Nary_operation"], RELATIONS, DATA)
    assert m["is_association"] and m["arity"] == 3
    assert m["foldable"] is False
    assert len(m["key_cols"]) == 3


def test_join_with_own_columns_is_not_foldable():
    m = so.association_meta(ENTS["Nary_sales"], RELATIONS, DATA)
    assert m["is_association"] and m["arity"] == 2
    assert m["has_own_columns"] is True and m["foldable"] is False
    assert m["key_cols"] == ["id"]  # 有独立主键 → 仍用主键


def test_plain_entity_is_not_association():
    m = so.association_meta(ENTS["Nary_batches"], RELATIONS, DATA)
    assert m["is_association"] is False


# ── 2. 建本结果：三元关联实体不得折叠 ───────────────────────────────────────────
def _nt():
    return "\n".join(so.to_nt(DATA, dict(SCHEMA)))


def test_ternary_keeps_association_instances():
    txt = _nt()
    # 三条参与边都从关联实体出发（不能退化成 batch→raw / batch→equip 直连）
    assert "Nary_operation_B1-E1-R1" in txt
    assert txt.count("Nary_operation_B1-E1-R1>") >= 4  # type + 3 条参与边


def test_ternary_participant_edges_complete():
    txt = _nt()
    for p in ("opBatch", "opRaw", "opEquip"):
        assert p in txt, "缺少参与方属性 " + p
    # 三个实例，每个一条类型声明 + 三条参与边 = 12 行
    lines = [ln for ln in txt.splitlines() if "Nary_operation_" in ln]
    assert len(lines) >= 3 * 4


def test_ternary_class_declared_as_association():
    txt = _nt()
    assert "#Association>" in txt
    assert "3 元关联" in txt


def test_binary_detail_table_collapses():
    txt = _nt()
    # detail=True 且 arity=2 → 折叠：父批次直接连原料，不出现连接实体实例
    assert "Nary_ingredient_B1-R1" not in txt
    assert "ingRaw" in txt


# ── 3. IRI 稳定性：行序变化不影响既有实体身份 ───────────────────────────────────
def test_assoc_iri_is_row_order_independent():
    shuffled = dict(DATA)
    shuffled["nary_operation"] = list(reversed(DATA["nary_operation"]))
    a = {ln.split(">")[0] for ln in so.to_nt(DATA, dict(SCHEMA)) if "Nary_operation_" in ln}
    b = {ln.split(">")[0] for ln in so.to_nt(shuffled, dict(SCHEMA)) if "Nary_operation_" in ln}
    assert a == b


def test_assoc_iri_has_no_row_index_suffix():
    txt = _nt()
    for name in ("Nary_operation_B1-E1-R1", "Nary_operation_B2-E1-R1"):
        assert name in txt
    # 不应出现 _0/_1 这种行序兜底后缀（除非真有重复行）
    assert "Nary_operation_B1-E1-R1_0" not in txt


# ── 4. 回归：二元关联折叠后仍能反查 ─────────────────────────────────────────────
def test_binary_fold_keeps_both_outputs():
    txt = _nt()
    # 折叠不产生连接实体，但参与方关系仍完整
    assert "Nary_batches_B1" in txt
    assert "Nary_raws_R1" in txt
