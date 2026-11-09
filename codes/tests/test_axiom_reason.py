# -*- coding: utf-8 -*-
"""test_axiom_reason.py — 公理集推理机（axiom_reason.py）单测。

全部用临时目录构造合成数据，纯标准库 + pytest，hermetic。
覆盖：类型继承 / domain-range 补充与端点冲突跳过 / 逆属性 / 属性链 /
业务规则传播与改写记账 / 单值冲突 / 不相交冲突 / 引用断裂修复 / 不动点终止 / CLI 落盘。
"""
import io
import json
import os
import sys

import pytest

_CODES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODES_DIR not in sys.path:
    sys.path.insert(0, _CODES_DIR)

import axiom_reason as ar  # noqa: E402

P = "http://ex/onto#"
T = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
SUB = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


def _p(p):
    return p if p.startswith("http") else P + p


def nt(s, p, o):
    return "<%s%s> <%s> <%s%s> ." % (P, s, _p(p), P, o)


def ntl(s, p, v):
    return '<%s%s> <%s> "%s" .' % (P, s, _p(p), v)


def write(tmp, lines, schema=None, axioms=None):
    ntp = os.path.join(str(tmp), "t.nt")
    with io.open(ntp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    sp = os.path.join(str(tmp), "schema.json")
    with io.open(sp, "w", encoding="utf-8") as f:
        json.dump(schema or {}, f, ensure_ascii=False)
    ap = os.path.join(str(tmp), "axioms.json")
    with io.open(ap, "w", encoding="utf-8") as f:
        json.dump(axioms or {}, f, ensure_ascii=False)
    return ntp, sp, ap


def run(tmp, lines, schema=None, axioms=None, extra=None):
    ntp, sp, ap = write(tmp, lines, schema, axioms)
    out = os.path.join(str(tmp), "out")
    argv = ["--nt", ntp, "--schema", sp, "--axioms", ap, "--out-dir", out, "--quiet"]
    assert ar.main(argv + (extra or [])) == 0
    with io.open(os.path.join(out, "report.json"), encoding="utf-8") as f:
        return json.load(f), out


SCHEMA = {
    "entities": [
        {"id": "Widget", "table": "widget"},
        {"id": "Order", "table": "order"},
    ],
    "relations": [{"id": "inOrder", "from": "Order", "to": "Widget"}],
}
DERIVE = {"prefix": P, "derive": {"domain_range": True}}


# ───────────────────────── 类型 ─────────────────────────

def test_subclass_inheritance_on_individuals_and_classes(tmp_path):
    lines = [
        nt("Widget", T, "Class"), nt("Item", T, "Class"), nt("Thing", T, "Class"),
        nt("Widget", SUB, "Item"), nt("Item", SUB, "Thing"),
        nt("w1", T, "Widget"),
    ]
    rep, _ = run(tmp_path, lines)
    inf = rep["inferred_sample"]
    axioms = [x["axiom"] for x in inf]
    assert "AX-RDFS-SUBCLASS" in axioms
    facts = " ".join(x["triple"] for x in inf)
    assert "w1> <%s> <%sItem>" % (T, P) in facts
    assert "w1> <%s> <%sThing>" % (T, P) in facts
    # 类层次也要传递：Widget ⊑ Thing
    assert "Widget> <%s> <%sThing>" % (SUB, P) in facts


def test_domain_range_fill_types_when_missing(tmp_path):
    lines = [nt("s1", "inOrder", "w1"), nt("w1", T, "Widget")]
    rep, _ = run(tmp_path, lines, SCHEMA, DERIVE)
    facts = " ".join(x["triple"] for x in rep["inferred_sample"])
    # 主语没类型 -> 按 domain 补 Order
    assert "s1> <%s> <%sOrder>" % (T, P) in facts


def test_endpoint_mismatch_skips_instead_of_wrong_type(tmp_path):
    """数据方向与 schema 相反时，不许硬套 domain，把订单标成产品。"""
    lines = [nt("s1", T, "Order"), nt("w1", T, "Widget"), nt("s1", "inOrder", "w1")]

    # 方向一致：domain=Order，s1 已是 Order -> 相容
    ok_schema = {
        "entities": [{"id": "Widget", "table": "widget"}, {"id": "Order", "table": "order"}],
        "relations": [{"id": "inOrder", "from": "Order", "to": "Widget"}],
    }
    rep_ok, _ = run(tmp_path, lines, ok_schema, DERIVE)
    assert not rep_ok["endpoint_mismatch"]

    # 方向反了：schema 说 domain=Widget，数据里主语是 Order -> 必须跳过
    bad_schema = {
        "entities": [{"id": "Widget", "table": "widget"}, {"id": "Order", "table": "order"}],
        "relations": [{"id": "inOrder", "from": "Widget", "to": "Order"}],
    }
    rep, _ = run(tmp_path, lines, bad_schema, DERIVE)
    assert rep["endpoint_mismatch"], "端点不符必须被记录"
    m = rep["endpoint_mismatch"][0]
    assert m["prop"] == "inOrder" and m["kind"] in ("domain", "range")
    wrong = [x for x in rep["inferred_sample"]
             if x["axiom"].startswith("AX-DR-inOrder") and "Widget>" in x["triple"]
             and "s1>" in x["triple"]]
    assert not wrong, "不得把订单推成产品"


# ───────────────────────── 属性公理 ─────────────────────────

def test_inverse_property(tmp_path):
    lines = [nt("a", T, "Widget"), nt("b", T, "Order"), nt("a", "parentOf", "b")]
    axioms = {"prefix": P, "inverse_of": [["parentOf", "childOf"]]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    facts = " ".join(x["triple"] for x in rep["inferred_sample"])
    assert "b> <%schildOf> <%sa>" % (P, P) in facts


def test_property_chain_forward_and_inverse(tmp_path):
    lines = [
        nt("b1", T, "Batch"), nt("p1", T, "Product"), nt("r1", T, "Raw"),
        nt("b1", "producedIn", "p1"), nt("b1", "usesRawMaterial", "r1"),
    ]
    axioms = {"prefix": P, "chains": [{
        "id": "AX-C01", "path": [{"prop": "producedIn", "inverse": True},
                                 {"prop": "usesRawMaterial"}], "implies": "hasMaterial"}]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    facts = " ".join(x["triple"] for x in rep["inferred_sample"])
    assert "p1> <%shasMaterial> <%sr1>" % (P, P) in facts


def test_functional_conflict(tmp_path):
    lines = [nt("w1", T, "Widget"), ntl("w1", "modelCode", "A-1"),
             ntl("w1", "modelCode", "A-2")]
    axioms = {"prefix": P, "functional": [{"id": "AX-F01", "prop": "modelCode"}]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    assert rep["stats"]["functional_conflicts"] == 1
    assert rep["stats"]["functional_detail"][0]["values"] == ["A-1", "A-2"]


def test_disjoint_conflict(tmp_path):
    lines = [nt("A", T, "Class"), nt("B", T, "Class"), nt("x", T, "A"), nt("x", T, "B")]
    axioms = {"prefix": P, "disjoint_with": [{"id": "AX-D01", "classes": ["A", "B"]}]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    assert rep["stats"]["disjoint_conflicts"] == 1
    assert rep["stats"]["disjoint_detail"][0]["individual"] == "x"


# ───────────────────────── 业务规则 ─────────────────────────

def test_business_rule_propagates_along_link(tmp_path):
    lines = [
        nt("q1", T, "Qc"), ntl("q1", "result", "不合格"),
        nt("b1", T, "Batch"), nt("q1", "checkedBy", "b1"),
    ]
    axioms = {"prefix": P, "business_rules": [{
        "id": "BR-01", "when": {"class": "Qc", "prop": "result", "op": "eq", "value": "不合格"},
        "then": {"link": "checkedBy", "target_class": "Batch", "prop": "status", "value": "待处理"}}]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    facts = " ".join(x["triple"] for x in rep["inferred_sample"])
    assert '"待处理"' in facts and "b1>" in facts


def test_business_rule_rewrite_is_recorded_not_silent(tmp_path):
    lines = [
        nt("q1", T, "Qc"), ntl("q1", "result", "不合格"),
        nt("b1", T, "Batch"), nt("q1", "checkedBy", "b1"), ntl("b1", "status", "已入库"),
    ]
    axioms = {"prefix": P, "business_rules": [{
        "id": "BR-01", "when": {"class": "Qc", "prop": "result", "op": "eq", "value": "不合格"},
        "then": {"link": "checkedBy", "target_class": "Batch", "prop": "status", "value": "待处理"}}]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    assert rep["rule_conflicts"], "改写既有状态必须留痕"
    c = rep["rule_conflicts"][0]
    assert c["before"] == ["已入库"] and c["after"] == "待处理"


def test_business_rule_numeric_gt(tmp_path):
    lines = [nt("e1", T, "Equip"), ntl("e1", "powerKw", "150"),
             nt("e2", T, "Equip"), ntl("e2", "powerKw", "20")]
    axioms = {"prefix": P, "business_rules": [{
        "id": "BR-03", "when": {"class": "Equip", "prop": "powerKw", "op": "gt", "value": 100},
        "then": {"prop": "equipmentClass", "value": "重点设备"}}]}
    rep, _ = run(tmp_path, lines, {}, axioms)
    facts = " ".join(x["triple"] for x in rep["inferred_sample"])
    assert "e1> <%sequipmentClass>" % P in facts
    assert "e2> <%sequipmentClass>" % P not in facts


# ───────────────────────── 引用完整性 ─────────────────────────

def test_dangling_reference_repaired_when_unique(tmp_path):
    """导出器把宾语写成「引用方表_P006」，真实个体叫「目标表_P006」。尾段唯一即修。"""
    lines = [
        nt("w_P1", T, "Widget"),
        nt("s2", T, "Order"), nt("s2", "inOrder", "gone_P1"),
    ]
    rep, _ = run(tmp_path, lines, {})
    assert rep["repaired_refs"], "应修正唯一同名引用"
    assert rep["repaired_refs"][0]["fixed"] == "w_P1"
    assert rep["repaired_refs"][0]["broken"] == "gone_P1"


def test_dangling_ambiguous_not_repaired(tmp_path):
    lines = [
        nt("w_P1", T, "Widget"), nt("x_P1", T, "Widget"),
        nt("s2", T, "Order"), nt("s2", "inOrder", "gone_P1"),
    ]
    rep, _ = run(tmp_path, lines, {})
    assert not rep["repaired_refs"], "同名候选不唯一时不许猜"


def test_no_fix_dangling_flag(tmp_path):
    lines = [nt("w_P1", T, "Widget"), nt("s2", T, "Order"),
             nt("s2", "inOrder", "gone_P1")]
    rep, _ = run(tmp_path, lines, {}, {}, extra=["--no-fix-dangling"])
    assert not rep["repaired_refs"]


# ───────────────────────── 终止性与落盘 ─────────────────────────

def test_fixpoint_terminates_within_round_limit(tmp_path):
    lines = [nt("A", T, "Class"), nt("B", T, "Class"), nt("A", SUB, "B"), nt("x", T, "A")]
    rep, out = run(tmp_path, lines, {}, {}, extra=["--max-rounds", "3"])
    assert os.path.exists(os.path.join(out, "report.json"))
    assert os.path.exists(os.path.join(out, "inferred.nt"))
    assert os.path.exists(os.path.join(out, "report.md"))


def test_input_file_untouched(tmp_path):
    lines = [nt("w1", T, "Widget"), ntl("w1", "modelCode", "A-1")]
    ntp, _, _ = write(tmp_path, lines)
    before = io.open(ntp, encoding="utf-8").read()
    run(tmp_path, lines)
    assert io.open(ntp, encoding="utf-8").read() == before


def test_bad_lines_counted_not_fatal(tmp_path):
    lines = [nt("w1", T, "Widget"), "this is not n-triples"]
    rep, _ = run(tmp_path, lines)
    assert rep["bad_lines"] == 1
