#!/usr/bin/env python3
"""词典碎片与对齐门的回归用例（P2-10）。

重点不是"能跑"，而是**不误报**：
  · 引擎本来就支持 camelCase（`deviceName` 能取到 `device_name`），本门必须同样放过；
  · 重映射只能采纳可解释的对应（去掉库名前缀后精确相等），不能按尾段乱连。

运行: cd codes && python -m pytest tests/test_lexicon_align.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "scripts"))

import pytest  # noqa: E402
import verify_lexicon_align as VA  # noqa: E402


# ── 归一化 ────────────────────────────────────────────────────────────────────
def test_norm_strips_unit_suffix():
    assert VA._norm("口径(mm)") == "口径"
    assert VA._norm("压力等级（PN）") == "压力等级"
    assert VA._norm(" 产品 名称 ") == "产品名称"


def test_canon_treats_camel_and_snake_same():
    assert VA._canon("deviceName") == VA._canon("device_name")
    assert VA._canon("precision_grade") == VA._canon("precisionGrade")


# ── 检查：不误报 camelCase，真错配要报 ────────────────────────────────────────
def _patch(monkeypatch, tables, cols):
    monkeypatch.setattr(VA, "_tables", lambda kb: set(tables))
    monkeypatch.setattr(VA, "_cols", lambda kb: {t: set(c) for t, c in cols.items()})


def test_camelcase_lexicon_is_not_error(monkeypatch):
    _patch(monkeypatch, {"bellows_tube"}, {"bellows_tube": {"device_name", "device_type"}})
    lex = {"attr_cn2en": {"设备名称": "deviceName", "设备类型": "deviceType"}}
    errs, _w = VA.check_kb("bellows", lex, [], {}, None)
    assert errs == [], "引擎支持 camelCase，本门不得误报：%s" % errs


def test_wrong_table_is_error(monkeypatch):
    _patch(monkeypatch, {"chem_equipment"}, {"chem_equipment": {"device_name", "status"}})
    lex = {"entity_cn2en": {"设备": "mach_equipment"}}
    errs, _w = VA.check_kb("chem", lex, [], {}, None)
    assert any(e[0].startswith("E2") for e in errs)


def test_wrong_column_is_error(monkeypatch):
    _patch(monkeypatch, {"chem_equipment"}, {"chem_equipment": {"device_name", "status"}})
    lex = {"attr_cn2en": {"功率": "power_kw"}}
    errs, _w = VA.check_kb("chem", lex, [], {}, None)
    assert any(e[0].startswith("E3") for e in errs)


def test_entity_pointing_at_column_is_ok(monkeypatch):
    """`供应商` 在库内是某表的列而非独立表 —— 合法，不该报。"""
    _patch(monkeypatch, {"valve_raw_materials"},
           {"valve_raw_materials": {"id", "part_name", "supplier"}})
    lex = {"entity_cn2en": {"供应商": "supplier"}}
    errs, _w = VA.check_kb("valve", lex, [], {}, None)
    assert errs == []


def test_same_norm_key_two_columns_is_error(monkeypatch):
    _patch(monkeypatch, {"p"}, {"p": {"size_mm", "size_inch"}})
    lex = {"attr_cn2en": {"口径(mm)": "size_mm", "口径": "size_inch"}}
    errs, _w = VA.check_kb("p", lex, [], {}, None)
    assert any(e[0].startswith("E4") for e in errs)


# ── 重映射：只认"库名前缀 + 精确余名" ─────────────────────────────────────────
def test_remap_strips_known_library_prefix():
    assert VA._remap_candidate("internal_equipment", {"equipment", "line"}, "manufacturing") == "equipment"
    assert VA._remap_candidate("mach_products", {"chem_products", "chem_batches"}, "chem") == "chem_products"


def test_remap_rejects_tail_only_match():
    """payment_status 的尾段也是 status，但那是付款状态，不能连到设备状态。"""
    assert VA._remap_candidate("payment_status", {"equipment", "status"}, "x") is None


def test_remap_rejects_ambiguous():
    assert VA._remap_candidate("mach_line", {"line_a", "line_b"}, "m") is None


# ── 落库：停用留证 + 幂等 ─────────────────────────────────────────────────────
def test_prune_disables_with_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(VA, "CFG", str(tmp_path))
    monkeypatch.setattr(VA, "_tables", lambda kb: {"t_a"})
    monkeypatch.setattr(VA, "_cols", lambda kb: {"t_a": {"id", "status"}})
    lex = {"entity_cn2en": {"客户": "customer"}, "attr_cn2en": {"状态": "status", "金额": "amount"}}
    (tmp_path / "lexicon_k.json").write_text(json.dumps(lex, ensure_ascii=False), encoding="utf-8")

    n = VA.prune_kb("k", lex)
    assert n == 2  # 客户、金额
    assert "客户" not in lex["entity_cn2en"]
    assert "金额" not in lex["attr_cn2en"]
    assert "状态" in lex["attr_cn2en"], "合法映射不能被动"
    dis = lex["_disabled_by_align"]
    assert {d["cn"] for d in dis} == {"客户", "金额"}
    assert all(d.get("reason") for d in dis), "停用必须写明原因"

    saved = json.loads((tmp_path / "lexicon_k.json").read_text(encoding="utf-8"))
    assert "客户" not in saved["entity_cn2en"]
    assert (tmp_path / "lexicon_k.json.bak").exists(), "改前必须备份"

    assert VA.prune_kb("k", lex) == 0, "幂等：再跑一次无事可做"
