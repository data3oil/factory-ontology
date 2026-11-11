# -*- coding: utf-8 -*-
"""证据层融合的零行为变化证明：
  同一份 result，分别在"有证据库"与"无证据库"两种环境下过 envelope_from_result，
  逐键比对键集合，差异必须恰好是新增的 evidence_cites / evidence_verdict。
"""
import json
import os
import sys

sys.path.insert(0, r"D:/factory-ontology/codes")
os.chdir(r"D:/factory-ontology/codes")

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
    with_db = run(r"D:/factory-ontology/codes/data/evidence")
    without_db = run(r"C:/Users/zjjem/AppData/Local/hermes/cache/scratch/no_such_dir")
    a, b = set(with_db), set(without_db)
    print("有库键数 %d ｜ 无库键数 %d" % (len(a), len(b)))
    print("新增键：", sorted(a - b))
    print("丢失键：", sorted(b - a))
    same = all(json.dumps(with_db.get(k), sort_keys=True, default=str)
               == json.dumps(without_db.get(k), sort_keys=True, default=str) for k in (b - (a - b)))
    print("老字段逐字段一致：", same)
    if "evidence_cites" in with_db:
        top = with_db["evidence_cites"][0]
        print("首条证据：", top["path"], top["line_from"], "-", top["line_to"],
              "｜判定：", with_db.get("evidence_verdict"))
    else:
        print("未挂上证据（检查 EVIDENCE_KB_HOME 与库文件）")
