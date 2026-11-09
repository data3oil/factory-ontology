# -*- coding: utf-8 -*-
"""test_kb_register.py — kbs.json 注册的两条约定（跨平台 / 幂等不冲字段）。

kbs.json 是**入库**文件：里面存的 nt/lexicon 相对路径必须是正斜杠。
否则在 Windows 上跑一次 multi_model.build() 就会把它写成 ``output\\valve.nt``，
提交之后 Linux/macOS 侧拼出来的路径不存在，问答直接找不到本体。
api_server._update_kbs 一直做这个归一化，multi_model._register_kb 曾漏掉
（2026-11-10 补上，用本文件锁住）。
"""

import io
import json
import os
import sys

_CODES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODES not in sys.path:
    sys.path.insert(0, _CODES)

import multi_model as mm  # noqa: E402


def _kbs_file(tmp_path, content='{"kbs": {}}'):
    cfg = tmp_path / "config"
    cfg.mkdir()
    f = cfg / "kbs.json"
    f.write_text(content, encoding="utf-8")
    return f


def _entry(f, kb="valve"):
    with io.open(str(f), encoding="utf-8") as fh:
        return json.load(fh)["kbs"][kb]


def test_register_kb_normalizes_to_forward_slash(tmp_path, monkeypatch):
    """Windows 反斜杠必须被归一化成 /，否则入库配置在别的平台是坏路径。"""
    f = _kbs_file(tmp_path)
    monkeypatch.setattr(mm, "ROOT", str(tmp_path))

    mm._register_kb("valve",
                    os.path.join("output", "valve.nt"),
                    os.path.join("config", "lexicon_valve.json"))

    got = _entry(f)
    assert got["nt"] == "output/valve.nt"
    assert "\\" not in got["nt"], got["nt"]
    assert got["lexicon"] == "lexicon_valve.json"


def test_register_kb_upsert_keeps_existing_fields(tmp_path, monkeypatch):
    """幂等 upsert：只补 nt/lexicon，不能把已有 name/examples 冲掉。"""
    f = _kbs_file(tmp_path, json.dumps(
        {"kbs": {"valve": {"name": "阀门制造问答", "examples": ["有多少台设备"], "data_dir": "data_valve"}}},
        ensure_ascii=False))
    monkeypatch.setattr(mm, "ROOT", str(tmp_path))

    mm._register_kb("valve", "output/valve.nt", "config/lexicon_valve.json")

    got = _entry(f)
    assert got["name"] == "阀门制造问答"
    assert got["examples"] == ["有多少台设备"]
    assert got["data_dir"] == "data_valve"
    assert got["nt"] == "output/valve.nt"
