# -*- coding: utf-8 -*-
"""test_build_gate.py — 建库门禁：词典合并 + 约束校验 fail-closed。

守两条：①第二次建模不能把人工确认过的词典冲掉；②主键重复时不许建出库来。
"""
import os
import sys

_CODES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODES_DIR not in sys.path:
    sys.path.insert(0, _CODES_DIR)

import lexicon as lx  # noqa: E402
import run as run_mod  # noqa: E402


def test_merge_keeps_old_and_records_conflict():
    old = {"attr_cn2en": {"功率": "powerKw"}, "review": {"pending": [{"name": "压力"}]}}
    new = {"attr_cn2en": {"功率": "power", "流量": "flow"}, "review": {}}
    out = lx.merge_lexicon(old, new)

    assert out["attr_cn2en"]["功率"] == "powerKw", "旧值（可能人工确认过）优先"
    assert out["attr_cn2en"]["流量"] == "flow", "新字段要补进来"
    assert out["review"]["pending"], "原来的待复核项不能被清空"
    assert any(c["name"] == "功率" for c in out["review"]["pending"]), "冲突要记账"
    assert out["merged_from_previous"] is True


class _FakeSO:
    def __init__(self, errors):
        self.errors = errors
        self.nt_called = False

    def load_all(self, _p):
        return {"t": [{"id": "1"}]}

    def load_schema(self, _p):
        return {"_entities": {}}

    def validate(self, _data, _schema):
        return self.errors

    def to_nt(self, data, schema, outpath=None):      # noqa: ARG002
        self.nt_called = True
        return []

    def build_graph(self, *a, **k):                   # noqa: ARG002
        return {"nodes": {}, "edges": []}

    def build_ontology_model(self, *a, **k):          # noqa: ARG002
        return {"type_hierarchy": [], "semantic_domains": []}


def _run_with(monkeypatch, errors, force=False):
    fake = _FakeSO(errors)
    monkeypatch.setattr(run_mod, "_load", lambda *_a, **_k: fake)
    nt, lex = run_mod.setup_schema(_CODES_DIR, os.path.join(_CODES_DIR, "config",
                                                            "ontology_schema.json"),
                                   table="_gate_test", force=force)
    return fake, nt, lex


def test_error_blocks_build(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "OUT", str(tmp_path))
    errs = [{"severity": "error", "type": "unique", "msg": "实体 Product 主键重复: P001"}]
    fake, nt, lex = _run_with(monkeypatch, errs)
    assert (nt, lex) == (None, None)
    assert fake.nt_called is False, "校验不过时不许建库"


def test_warn_only_still_builds(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "OUT", str(tmp_path))
    warn = [{"severity": "warn", "type": "positive", "msg": "价格为负"}]
    fake, nt, lex = _run_with(monkeypatch, warn, force=False)
    assert fake.nt_called is True


def test_force_builds_despite_error(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "OUT", str(tmp_path))
    errs = [{"severity": "error", "type": "unique", "msg": "实体 Product 主键重复: P001"}]
    fake, _nt, _lex = _run_with(monkeypatch, errs, force=True)
    assert fake.nt_called is True
