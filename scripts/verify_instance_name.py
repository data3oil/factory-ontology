# -*- coding: utf-8 -*-
"""verify_instance_name.py — 实例名当实体用的回归门

背景（2026-11-22 审查）：问「塔里木三维区块B的炮点数是多少」时，
守卫只认「字母+数字编号」与 8 个类名，实例名不在索引里 →
判成"没点名实体"，落进取值过滤兜底，答出与问题无关的「符合条件共 2 条」。

本门用地震样例库断言四种问法都答出真实属性值，且不再出现无关计数。
纯标准库 + 进程内调用，不启服务。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "codes"))
os.chdir(os.path.join(ROOT, "codes"))
import ontology_qa_v3 as v3  # noqa: E402

NT = os.path.join(ROOT, "codes", "output", "seismic.nt")
LEX = os.path.join(ROOT, "codes", "config", "lexicon_seismic.json")

CASES = [
    ("塔里木三维区块B的炮点数是多少", "12000"),
    ("塔里木三维区块B的炮点数", "12000"),
    ("鄂尔多斯三维区块A的炮点数", "8500"),
    ("准噶尔三维区块D的炮点数", "6500"),
    ("测线塔里木三维区块B的炮点数", "12000"),
]


def main():
    if not (os.path.exists(NT) and os.path.exists(LEX)):
        print("SKIP: 缺 seismic 样例库")
        return 0
    D = json.load(open(LEX, encoding="utf-8"))
    QDATA = v3.build_data(v3.parse_nt(NT), D)
    bad = []
    for q, want in CASES:
        ans = str(v3.answer(q, QDATA, D))
        ok = want in ans and not re.search(r"符合条件共|暂不支持|未找到", ans)
        print(("  [PASS] " if ok else "  [FAIL] ") + q + " -> " + ans[:50].replace("\n", " "))
        if not ok:
            bad.append((q, ans[:60]))
    print("自检结果: %s（%d/%d）" % ("PASS" if not bad else "FAIL", len(CASES) - len(bad), len(CASES)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
