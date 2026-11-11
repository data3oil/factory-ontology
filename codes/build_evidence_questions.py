# -*- coding: utf-8 -*-
"""造证据层题集：先校验 must 在库中同段共现，不合格的当场剔除；另造一组库里确实没有的拒答题。"""
import io
import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(_HERE, "data", "evidence", "default.db")
OUT = os.path.join(_HERE, "tests", "fixtures", "evidence_questions.json")

CAND = [
    ("本体公理有哪几种", ["公理"]),
    ("四层结构是怎么分的", ["四层"]),
    ("为什么不是给阀门写死一套", ["写死"]),
    ("问答链路怎么接到公理的", ["问答"]),
    ("词表错配清单一共几条", ["15 条"]),
    ("代码层对枚举做了哪些兜底", ["_find_enum"]),
    ("CSV 数据质量问题的根因是什么", ["根因"]),
    ("修复前后分数怎么对比", ["复测"]),
    ("数据质量修复涉及哪些文件", ["涉及文件"]),
    ("本体和词典是什么关系", ["词典"]),
    ("部署要配哪些东西", ["部署"]),
    ("测试方案包含哪些内容", ["测试"]),
    ("本地与远端差异清单里提了什么", ["差异"]),
    ("本体检查都检查哪些项", ["检查"]),
    ("导出器抓到哪几个问题", ["导出"]),
    ("本体驱动与纯向量 RAG 的差别", ["RAG"]),
    ("推理机支持什么类型的规则", ["规则"]),
    ("知识库按 kb 隔离怎么实现的", ["kb"]),
]

REFUSALS = [
    "数控机床的刀具寿命怎么计算",
    "厂区消防喷淋压力标准是多少",
    "供应商年度审核流程怎么走",
    "员工差旅报销标准是多少",
    "注塑机模温机的温控曲线怎么设",
]

c = sqlite3.connect(DB)
ok, bad = [], []
for q, must in CAND:
    n = c.execute("SELECT count(*) FROM segs WHERE " + " AND ".join("body LIKE ?" for _ in must),
                  ["%" + m + "%" for m in must]).fetchone()[0]
    (ok if n else bad).append((q, must, n))

absent, present = [], []
for q in REFUSALS:
    terms = [t for t in q.replace("的", " ").split() if len(t) >= 2][:4]
    hit = 0
    for t in terms:
        hit += c.execute("SELECT count(*) FROM segs WHERE body LIKE ?", ("%" + t + "%",)).fetchone()[0]
    (absent if hit == 0 else present).append(q)
c.close()

print("题集候选 %d；同段可用 %d；剔除 %d" % (len(CAND), len(ok), len(bad)))
for q, must, n in bad:
    print("   剔除：%s（must=%s 同段 %d）" % (q[:26], must, n))
print("拒答候选 %d；确认库里没有 %d；实际有相关词剔除 %d" % (len(REFUSALS), len(absent), len(present)))
for q in present:
    print("   剔除：", q)
io.open(OUT, "w", encoding="utf-8").write(json.dumps(
    {"_说明": "证据层验收题集：questions 为可答题（must 已核实同段存在），refusals 为库里确无的问题",
     "questions": [{"q": q, "must": m} for q, m, _ in ok],
     "refusals": absent}, ensure_ascii=False, indent=1))
print("写入", OUT, "：可答", len(ok), "拒答", len(absent))
