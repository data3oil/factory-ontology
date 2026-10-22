# -*- coding: utf-8 -*-
"""问答层共享常量 —— 未命中/无据哨兵的单一真相源。

背景(B3 收口): "未命中"哨兵 `暂不支持该问题` 曾以字面量散落在
ontology_qa_v3 / api_server / mcp_server / run.py 四处做路由判据, 任一处改文案
就会让路由静默错乱(如规则引擎已返回新文案, 上层仍按旧字面量判定"未命中");
miss 关键词表也在 ontology_qa_v3 与 ask_service 各存一份且略有出入。
统一收口到本模块, 其余模块只引用常量, 不再写字面量。

本模块零依赖(纯标准库), 可被动态加载器(spec_from_file_location)安全导入。
"""

# 规则引擎"答不了"时返回的确定性哨兵。它同时被上层用作路由判据
# (ans != MISS_ANSWER 才认为规则引擎命中)。**不可随意改文案**: 改则所有引用同步生效。
MISS_ANSWER = "暂不支持该问题"

# 判定"未命中(拒答/无据, ≠ 误答/编造)"的特征词。短答案含任一即视为未命中。
# 与历史 ontology_qa_v3._MISS_MARKERS 口径完全一致(9 项), 作为唯一来源。
MISS_MARKERS = (
    "暂不支持", "无相关数据", "未找到", "没有找到", "不含所问",
    "无法回答", "未收录", "无该", "不存在",
)


def is_miss_answer(ans):
    """规则引擎输出是否等于"答不了"哨兵(路由判据, 忽略首尾空白)。"""
    return (ans or "").strip() == MISS_ANSWER


def is_miss_text(text):
    """文本是否呈现未命中特征(含任一 MISS_MARKERS); 空串视为未命中。"""
    t = (text or "").strip()
    if not t:
        return True
    return any(m in t for m in MISS_MARKERS)
