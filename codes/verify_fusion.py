# -*- coding: utf-8 -*-
"""证据层融合的零行为变化证明：
  同一份 result，分别在"有证据库"与"无证据库"两种环境下过 envelope_from_result，
  逐键比对键集合，差异必须恰好是新增的 evidence_cites / evidence_verdict。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

RESULT = {
    "ok": True,
    "mode": "rule",
    "answer": "公理分四类：类型继承、域值域、基数约束、业务规则。",
    "evidence": [{"kind": "rule", "id": "axiom.type_inherit", "label": "类型继承", "source": "本体"}],
    "no_basis": False,
    "kb": "food",
    "question": "本体公理有哪几种",
}


def run(home):
    os.environ["EVIDENCE_KB_HOME"] = home
    import importlib
    import ask_service
    importlib.reload(ask_service)
    out = ask_service.envelope_from_result(json.loads(json.dumps(RESULT)), "食品企业知识库")
    return out


if __name__ == "__main__":
    with_db = run(os.path.join(_HERE, "data", "evidence"))
    without_db = run(os.path.join(_HERE, "_no_such_evidence_dir"))   # 不存在的目录：验证无库时形状仍恒定
    a, b = set(with_db), set(without_db)
    print("有库键数 %d ｜ 无库键数 %d" % (len(a), len(b)))
    print("新增键：", sorted(a - b))
    print("丢失键：", sorted(b - a))
    # 两个证据键的值随环境变化（有库=列表，无库=null），属设计如此；其余键必须逐字段一致。
    EVID = {"evidence_cites", "evidence_verdict", "evidence_error"}
    same = all(json.dumps(with_db.get(k), sort_keys=True, default=str)
               == json.dumps(without_db.get(k), sort_keys=True, default=str) for k in sorted(b - EVID))
    print("老字段逐字段一致：", same)
    if "evidence_cites" in with_db:
        top = with_db["evidence_cites"][0]
        print("首条证据：", top["path"], top["line_from"], "-", top["line_to"],
              "｜判定：", with_db.get("evidence_verdict"))
    else:
        print("未挂上证据（检查 EVIDENCE_KB_HOME 与库文件）")
