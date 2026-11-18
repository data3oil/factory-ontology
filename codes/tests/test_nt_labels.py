#!/usr/bin/env python3
"""类别类必须带 rdfs:label 的回归（2026-11-18）。

背景：类别类成员 IRI 的局部名来自取值，中文会被 `_ascii_local` 编成 uXXXX
（生物农药 → u751fu7269u519cu836f）。这个编码本身是对的：可逆、确定、可移植。
但 nt 层若不附 rdfs:label，读的人只看到一串 uXXXX，反推不出中文，
TBox 也没法靠 label 做中英对齐。

`_nt_dimension_classes`（人确认的类型维度）一直带 label，`_nt_category_hierarchy`
（自动提的 category 列）漏了——本用例把这条锁住，防止再退回去。

运行: cd codes && python -m pytest tests/test_nt_labels.py -q
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)

import schema_ontology as so  # noqa: E402

DATA = {
    "chem_products": [
        {"id": "P1", "product_name": "除草剂甲", "category": "生物农药"},
        {"id": "P2", "product_name": "杀虫剂乙", "category": "化学农药"},
        {"id": "P3", "product_name": "杀菌剂丙", "category": "生物农药"},
    ]
}

LABEL_TRIPLE = re.compile(r'^<([^>]+)>\s+<http://www\.w3\.org/2000/01/rdf-schema#label>\s+"((?:[^"\\]|\\.)*)"')
CLASS_TRIPLE = re.compile(r'^<([^>]+)>\s+<http://www\.w3\.org/1999/02/22-rdf-syntax-ns#type>\s+<http://www\.w3\.org/2002/07/owl#Class>')


def _decode(local: str) -> str:
    """把 uXXXX 串还原成原文（与 _ascii_local 互为逆操作）。"""
    return re.sub(r"u([0-9a-f]{4})", lambda m: chr(int(m.group(1), 16)), local)


def _build():
    schema = so.suggest_schema(DATA, use_llm=False)
    return so.to_nt(DATA, dict(schema))


def test_chinese_category_class_is_ascii_encoded():
    """前提：中文取值确实被编码进 IRI 局部名（否则本用例测不到东西）。"""
    lines = _build()
    cls_iris = [m.group(1) for m in map(CLASS_TRIPLE.match, lines) if m]
    encoded = [u for u in cls_iris if re.search(r"u[0-9a-f]{4}", u.split("#")[-1])]
    assert encoded, "没有出现 uXXXX 编码的类 IRI，用例失效"


def test_every_encoded_class_has_label():
    """每个 uXXXX 类都必须有 rdfs:label，且 label 就是解码后的原文。"""
    lines = _build()
    labels = {}
    for ln in lines:
        m = LABEL_TRIPLE.match(ln)
        if m:
            labels[m.group(1)] = m.group(2)

    cls_iris = [m.group(1) for m in map(CLASS_TRIPLE.match, lines) if m]
    encoded = [u for u in cls_iris if re.search(r"u[0-9a-f]{4}", u.split("#")[-1])]
    missing = [u for u in encoded if u not in labels]
    assert not missing, "这些类别类缺 label: %s" % missing

    for u in encoded:
        local = u.split("#")[-1]
        # 类名形如 Chem_productsCategory_u751fu7269u519cu836f，取最后一段解码
        tail = re.search(r"(u[0-9a-f]{4}(?:u[0-9a-f]{4})*)$", local)
        if not tail:
            continue
        assert labels[u] == _decode(tail.group(1)), \
            "label 与 IRI 不一致: %s → %r" % (u, labels[u])


def test_category_parent_class_has_label():
    """父类 XxxCategory 也要有中文 label，否则 TBox 上一级仍是英文占位。"""
    lines = _build()
    labels = {}
    for ln in lines:
        m = LABEL_TRIPLE.match(ln)
        if m:
            labels[m.group(1)] = m.group(2)
    cat_parents = [m.group(1) for m in map(CLASS_TRIPLE.match, lines)
                   if m and m.group(1).split("#")[-1].endswith("Category")]
    assert cat_parents, "没生成 Category 父类，用例失效"
    missing = [u for u in cat_parents if u not in labels]
    assert not missing, "Category 父类缺 label: %s" % missing
