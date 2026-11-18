# -*- coding: utf-8 -*-
"""问法覆盖的回归护栏（2026-11-18 补）。

这一批改动的共同点：**不是算错，是问法没被认出来**。CQ 门实测 58 题里
33 题的失败原因都是"换成另一种说法就答得出"，例如：
  设备中有几台在运行   （量词不是"多少"）
  质检不合格的记录有几条（值落在 result 列，不是 status 列）
  生产批次一共有多少批  （实体词在前、量词在后的语序）
  口径最大的阀门是多少毫米（"多少毫米"被当成过滤值）

这些规则都是纯函数，用真值表锁住。改坏了会立刻红，不用等跑 58 题 CQ。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ontology_qa_v3 as v3  # noqa: E402


# ---------------------------------------------------------------- 计数意图
def test_count_intent_accepts_colloquial_measure_words():
    """口语量词必须算"在问数量"，否则整句拒答。"""
    for q in ("设备中有几台在运行？", "乳制品有几个？", "记录有几条？",
              "待检的批次有几批？", "不合格的有几种？", "客户有几家？",
              "订单一共有多少单？", "工单有几张？"):
        assert v3._is_count_q(q), q


def test_count_intent_rejects_bare_ji_and_value_questions():
    """裸"几"不是计数意图；问具体值（多少毫米）也不是。"""
    for q in ("几号阀门漏了？", "这是几号阀？", "这一批怎么样？"):
        assert not v3._is_count_q(q), q


def test_count_intent_still_matches_plain_duoshao():
    """原有"多少"路径不能因为加了量词覆盖而丢（避免修新忘旧）。"""
    assert v3._is_count_q("供应商有多少个？")
    assert v3._is_count_q("一共有多少条记录？")
    assert v3._is_count_q("设备总数是多少？")


# ---------------------------------------------------------------- 疑问短语
def test_interrogative_detects_unit_suffixed_questions():
    """`是多少毫米` 捕获到的是"多少毫米"，它**不是**可过滤的取值。

    不认这点就会答成「有 0 条（口径为多少毫米）」——把回答的形状当成过滤的值。
    """
    for tok in ("多少毫米", "多少升", "多少千克", "多少天", "多少", "什么", "几台"):
        assert v3._is_interrogative(tok), tok


def test_interrogative_accepts_real_values():
    for tok in ("PN16", "已交付", "华东", "R007", "H"):
        assert not v3._is_interrogative(tok), tok


# ---------------------------------------------------------------- 取值变体
def test_value_variants_adds_zai_form():
    """台账写"维护中"，人问"在维护" —— 这是同一状态的两种说法。"""
    assert v3._value_variants("维护中") == {"维护中", "在维护"}


def test_value_variants_never_returns_bare_stem():
    """🔴 裸词干绝不能进候选集。

    回归案例：`生产中` 的裸词干是"生产"，而「生产批次一共有多少批」里正好有"生产"，
    于是批次总数被答成「有 2 批生产中的」。裸词干是动词/名词的高频词素，
    必须要求"在"这个状态标记才能认。
    """
    for v in ("生产中", "运行中", "维护中"):
        stem = v[:-1]
        assert stem not in v3._value_variants(v), v
        assert ("在" + stem) in v3._value_variants(v), v


def test_value_variants_keeps_short_and_suffixed_values():
    assert v3._value_variants("在产") == {"在产"}      # 词干只剩 1 字，不加"在产"变体
    assert v3._value_variants("不合格") == {"不合格"}  # 无后缀


# ---------------------------------------------------------------- 取值即过滤
_ROWS = {
    "Qc_1": {"result": "合格", "note": "a"},
    "Qc_2": {"result": "不合格", "note": "b"},
    "Qc_3": {"result": "不合格", "note": "b"},
    "Qc_4": {"result": "合格", "note": "a"},
}


def test_value_eq_finds_column_the_value_actually_lives_in():
    """值不在 status 列也能找到 —— 由数据自己认"值在哪一列"。"""
    got = v3._value_eq_in_question("质检不合格的记录有多少条？", _ROWS, {})
    assert got is not None
    col, val, n = got
    assert (col, val, n) == ("result", "不合格", 2)


def test_value_eq_prefers_longer_value():
    """"不合格"(3字) 必须压过同列的"合格"(2字)，否则答成 2 条合格的。"""
    col, val, _n = v3._value_eq_in_question("不合格的有几条？", _ROWS, {})
    assert val == "不合格"


def test_value_eq_ignores_ids_and_numbers():
    """编号/数值列不是业务取值，不能当过滤条件。"""
    rows = {"A_1": {"id": "A001", "qty": "10"}, "A_2": {"id": "A002", "qty": "20"}}
    assert v3._value_eq_in_question("A001 有多少条？", rows, {}) is None


def test_value_eq_returns_none_when_no_value_mentioned():
    assert v3._value_eq_in_question("一共有多少条？", _ROWS, {}) is None


# ---------------------------------------------------------------- 分组字段
def test_entity_name_is_not_a_group_field():
    """🔴 实体名不能当分组键。

    回归案例：attr_cn2en 里"客户"→customer_id，「客户分布在哪些地区」被按 8 个客户
    分组，输出「客户分布:」（空）。实体词只能限定范围，不能分组。
    """
    D = {"entity_cn2en": {"客户": "customers"}, "attr_cn2en": {"客户": "customer_id",
                                                              "地区": "region"}}
    en, cn, mode = v3._find_agg_attr(D, "客户分布在哪些地区？")
    assert cn != "客户"
    assert (en, mode) == ("region", "dist")


def test_group_pattern_still_works():
    D = {"attr_cn2en": {"车间": "workshop"}}
    assert v3._find_agg_attr(D, "各车间的设备数量") == ("workshop", "车间", "group")
