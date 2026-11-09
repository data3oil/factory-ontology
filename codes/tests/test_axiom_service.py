# -*- coding: utf-8 -*-
"""test_axiom_service.py — 公理服务层（axiom_service.py）单测。

覆盖：角色识别 / 路径搜索 / 种子生成 / 结构校验与保存 / 推理落盘 /
正反向作答 / 不命中返回 None / 接口层（/api/axioms 读存跑 + /api/ask 走到 axiom 引擎）。

除接口用例外全部 hermetic：临时目录 + 猴补 axiom_service 的路径函数，不依赖真实库。
"""

import io
import json
import os
import sys

import pytest

_CODES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODES not in sys.path:
    sys.path.insert(0, _CODES)

import axiom_service as axs  # noqa: E402

P = "http://ex/onto#"
T = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
SUB = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


def _t(s, p, o):
    return "<%s%s> <%s> <%s%s> ." % (P, s, p if p.startswith("http") else P + p, P, o)


def _tl(s, p, v):
    return '<%s%s> <%s> "%s" .' % (P, s, p if p.startswith("http") else P + p, v)


def _fixture(tmp_path, monkeypatch):
    """一个跨行业的最小库：产品←批次→原料（经用料明细）+ 质检，数据里带不合格。"""
    nt = os.path.join(str(tmp_path), "kb.nt")
    lines = [
        _t("Product", T, "Class"), _t("Batch", T, "Class"), _t("Material", T, "Class"),
        _t("Ingredient", T, "Class"), _t("Qc", T, "Class"),
        _t("p1", T, "Product"), _tl("p1", "productName", "一号产品"), _tl("p1", "modelCode", "M-1"),
        _t("b1", T, "Batch"), _tl("b1", "status", "已入库"),
        _t("i1", T, "Ingredient"),
        _t("m1", T, "Material"), _tl("m1", "partName", "阀体"),
        _t("q1", T, "Qc"), _tl("q1", "result", "不合格"),
        _t("b1", "producedIn", "p1"),
        _t("i1", "belongsToBatch", "b1"),
        _t("i1", "usesRawMaterial", "m1"),
        _t("q1", "checkedBy", "b1"),
    ]
    io.open(nt, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    ax_path = os.path.join(str(tmp_path), "kb.json")
    monkeypatch.setattr(axs, "nt_path", lambda kb: nt)
    monkeypatch.setattr(axs, "axiom_path", lambda kb: ax_path)
    monkeypatch.setattr(axs, "reason_dir", lambda kb: os.path.join(str(tmp_path), "reason"))
    return nt, ax_path


# ───────────────────────── 角色与路径 ─────────────────────────

def test_role_detection_by_name():
    roles = axs.load_seed()["roles"]
    assert axs._role_of("Valve_products", roles) == "product"
    assert axs._role_of("Food_raw_materials", roles) == "material"
    assert axs._role_of("Food_batch_ingredient", roles) == "material_link"
    assert axs._role_of("Food_qc", roles) == "qc"
    # 状态细分不参与角色识别
    assert axs._role_of("Food_batches__running", roles) is None


def test_find_path_forward_and_inverse():
    edges = {("Product", "madeBy", "Batch"): 3, ("Batch", "uses", "Material"): 3}
    path = axs._find_path(edges, "Product", "Material", 4)
    assert path == [("madeBy", False), ("uses", False)]
    back = axs._find_path(edges, "Material", "Product", 4)
    assert back == [("uses", True), ("madeBy", True)]
    assert axs._find_path(edges, "Material", "Qc", 4) is None


# ───────────────────────── 生成 ─────────────────────────

def test_generate_finds_cross_table_chain(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    doc = axs.generate("t")
    assert doc["prefix"] == P
    assert doc["seed_report"]["roles"]["product"] == ["Product"]
    ch = [c for c in doc["chains"] if c["implies"] == "hasMaterial"]
    assert ch, "应生成 产品→原料 的链"
    path = ch[0]["path"]
    # Product → Batch（反向）→ Ingredient（反向）→ Material（正向）
    assert [(s["prop"], s.get("inverse")) for s in path] == [
        ("producedIn", True), ("belongsToBatch", True), ("usesRawMaterial", False)]


def test_generate_makes_qc_rule_from_real_values(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    doc = axs.generate("t")
    br = doc["business_rules"]
    assert len(br) == 1
    assert br[0]["when"]["prop"] == "result" and br[0]["when"]["value"] == "不合格"
    assert br[0]["then"]["link"] == "checkedBy"


def test_generate_without_data_is_empty_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(axs, "nt_path", lambda kb: os.path.join(str(tmp_path), "none.nt"))
    monkeypatch.setattr(axs, "axiom_path", lambda kb: os.path.join(str(tmp_path), "x.json"))
    doc = axs.generate("t")
    assert doc["chains"] == [] and doc["seed_report"]["skipped"]


# ───────────────────────── 校验与保存 ─────────────────────────

def test_validate_rejects_broken_docs():
    assert axs.validate([]) is not None
    assert axs.validate({"chains": [{"implies": "x"}]}) is not None          # 缺 path
    assert axs.validate({"chains": [{"implies": "x", "path": [{}]}]}) is not None  # 步缺 prop
    assert axs.validate({"business_rules": [{"when": {}, "then": {}}]}) is not None
    assert axs.validate({"disjoint_with": [{"classes": ["A"]}]}) is not None
    assert axs.validate({"chains": [{"implies": "x", "path": [{"prop": "p"}]}]}) is None


def test_save_writes_and_backs_up(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    good = {"chains": [{"id": "A", "implies": "hasX", "path": [{"prop": "p"}]}]}
    ok, err = axs.save("t", good)
    assert ok, err
    ok, err = axs.save("t", {"chains": [{"id": "B", "implies": "hasY", "path": [{"prop": "q"}]}]})
    assert ok, err
    doc = json.loads(io.open(os.path.join(str(tmp_path), "kb.json"), encoding="utf-8").read())
    assert doc["chains"][0]["id"] == "B"
    assert any(f.startswith("kb.json.bak_") for f in os.listdir(str(tmp_path)))


# ───────────────────────── 推理与作答 ─────────────────────────

def test_run_writes_closure_and_reports(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    axs.save("t", axs.generate("t"))
    res = axs.run("t")
    assert res["ok"] and res["report"]["inferred"] > 0
    out = os.path.join(str(tmp_path), "reason")
    assert os.path.exists(os.path.join(out, "closure.nt"))
    assert os.path.exists(os.path.join(out, "report.json"))
    # 产品→原料 与 质检规则 都应推出来
    by = res["report"]["by_axiom"]
    assert any(k.startswith("SEED-C0") for k in by)
    assert any(k.startswith("SEED-BR") for k in by)


def test_answer_forward_and_reverse(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    axs.save("t", axs.generate("t"))
    fwd = axs.answer("p1 用了哪些原料", "t")
    assert fwd and "阀体" in fwd["answer"] and fwd["structured"]["direction"] == "forward"
    rev = axs.answer("m1 被哪些产品用到", "t")
    assert rev and rev["structured"]["direction"] == "reverse"


def test_answer_returns_none_when_no_intent(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    axs.save("t", axs.generate("t"))
    assert axs.answer("今天天气怎么样", "t") is None
    assert axs.answer("p1 的型号是什么", "t") is None      # 有实体但没问法 → 不抢规则引擎的活


# ───────────────────────── 接口层（闭环） ─────────────────────────

def test_routes_and_ask_loop(monkeypatch):
    os.environ.setdefault("FOOD_ADMIN_KEY", "test-admin-key")
    from fastapi.testclient import TestClient
    import api_server as core

    c = TestClient(core.app)
    h = {"X-API-Key": os.environ.get("FOOD_ADMIN_KEY", "test-admin-key")}

    r = c.get("/api/axioms?kb=valve", headers=h)
    assert r.status_code == 200 and r.json()["ok"]
    assert r.json()["axioms"]["chains"], "阀门库应有属性链"

    r = c.post("/api/axioms/reason", json={"kb": "valve"}, headers=h)
    assert r.status_code == 200 and r.json()["ok"]
    assert r.json()["report"]["inferred"] > 0

    # 闭环：问答走到 axiom 引擎，且答案带依据
    r = c.post("/api/ask", json={"question": "P004 用了哪些原料", "kb": "valve"}, headers=h)
    d = r.json()
    assert r.status_code == 200
    assert d["mode"] == "axiom" and "axiom" in (d.get("engines") or [])
    assert d["evidence"], "公理答案必须带证据"

    # 普通计数问题仍归规则引擎，公理引擎不越界
    r = c.post("/api/ask", json={"question": "有多少台设备", "kb": "valve"}, headers=h)
    assert r.json()["mode"] == "rule"


def test_routes_reject_broken_axioms():
    os.environ.setdefault("FOOD_ADMIN_KEY", "test-admin-key")
    from fastapi.testclient import TestClient
    import api_server as core

    c = TestClient(core.app)
    h = {"X-API-Key": os.environ.get("FOOD_ADMIN_KEY", "test-admin-key")}
    r = c.put("/api/axioms", json={"kb": "valve", "axioms": {"chains": [{"implies": "x"}]}},
              headers=h)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "path" in r.json()["error"]
