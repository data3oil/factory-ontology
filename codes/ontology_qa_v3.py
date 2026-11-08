#!/usr/bin/env python3
"""ontology_qa_v3.py — 词典驱动的通用问答引擎（精简方法论核心）。

设计目标：换任何数据源/工厂，只换词典，引擎代码不动。
原则：模板逻辑通用，不硬编码任何具体中文词；属性名、枚举值全部从词典读。

词典 (lexicon.json) 提供：
  attr_cn2en   中文属性名 -> 字段英文 (如 功率->powerKw)
  attr_en2cn   字段英文 -> 中文属性名 (显示用)
  status_cn2en 状态词 -> 值
  type_cn2en   类型词 -> 值
  zone_cn2en   区域词 -> 值 (可选)
  field_aliases 标准字段别名 (status/deviceType/deviceName/location)

通用模板（与具体词无关）：
  数量: "多少[台/个/条][状态词/类型词/区域词]" 或 "[词]的[词]共几"
  列出: "列出所有[类型/状态/区域]"
  极值: "[属性]最[大/小/高/低]"
  TopN: "[属性]最[高/低]的N[台/个]"
  平均: "[属性]平均"
  总和: "[属性]总[和/计]"
  范围: "[属性][大于/小于/在A到B之间]"
  分组: "统计按[属性]" 或 "按[属性]和[属性]统计"
  反查: "哪些[设备/物品][属性]=值"
  组合: "[状态词]的[类型词]"

用法: python ontology_qa_v3.py <nt文件> "<问题>" [lexicon.json]
"""

import sys
import os
import re
import json
import threading
from collections import defaultdict

# 词典数据收敛到 lexicon.py 唯一数据源（P1-7），仅持有只读引用，消除跨文件重复漂移。
from lexicon import (get_attr_cn_aliases, get_common_zh_status, get_entity_cn2uri,
                     get_field_words)

_FIELD_WORDS = get_field_words()


# ── 计算过程回传（证据不再事后重扫）────────────────────────────────
# 老做法：answer() 给出字符串，evidence.build_trace 拿着问题再"猜"一遍是谁算出来的，
# 于是规则表和引擎各写一套（deviceType 硬编码在猜的那一边），猜漏了就得出"命中但没证据"。
# 现在反过来：answer() 在真正选中记录的瞬间把它记下来，信封直接用这份记录。
_TRACE_LOCAL = threading.local()
_TRACE_LIMIT = 200      # 单次问答最多记 200 条：够溯源，又不至于把信封撑爆


def _trace_begin():
    _TRACE_LOCAL.sink = {"rows": [], "total": 0, "truncated": False}


def _trace_take():
    """取出本次计算的记录并关闭 sink（取完即清，避免跨请求串味）。"""
    sink = getattr(_TRACE_LOCAL, "sink", None)
    _TRACE_LOCAL.sink = None
    return sink


def _note_rows(rows, prop_cn, value_of=None):
    """记录"本次答案用到了哪些记录"，原样返回 rows，调用点仍是一行。

    rows 支持两种形态：[(名, 记录)] 与 [(记录, 数值)]。
    只记前 _TRACE_LIMIT 条，超出部分计入 total 并置 truncated —— 不做静默截断。
    """
    sink = getattr(_TRACE_LOCAL, "sink", None)
    if sink is None:
        return rows
    try:
        n = len(rows)
    except TypeError:
        return rows
    sink["total"] += n
    if n > _TRACE_LIMIT or len(sink["rows"]) + n > _TRACE_LIMIT:
        sink["truncated"] = True
    for item in rows:
        if len(sink["rows"]) >= _TRACE_LIMIT:
            break
        try:
            a, b = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        if isinstance(a, dict):
            rec, val = a, b
            name = _display_name(rec, {}, default="")
        else:
            name, rec = a, b
            val = value_of(rec) if callable(value_of) and isinstance(rec, dict) else None
        sink["rows"].append({
            "kind": "entity", "id": str(name),
            "label": "%s=%s" % (prop_cn, "" if val is None else val),
            "source": "rule",
        })
    return rows


def answer_traced(q, data, D):
    """答一次问题，同时带回"算的时候用到的记录"。返回 (答案, trace 或 None)。"""
    _trace_begin()
    try:
        text = answer(q, data, D)
    finally:
        sink = _trace_take()
    return text, sink


def _cn_of_field(field):
    """把英文列名还原成中文：device_type→设备类型、publisher→出版社、power_kw→功率。

    只做词素拼接，认不出的词素直接跳过（power_kw 只拼出"功率"，仍能对上"功率最大"）。
    用于词典缺中文名时的兜底——food 的设备表整张没有中文名，
    library 的 publisher 中文名就是 "publisher"。
    """
    parts = re.split(r"[_\-]|(?<=[a-z0-9])(?=[A-Z])", str(field or ""))
    out = []
    for p in parts:
        w = p.strip().lower()
        if w and w in _FIELD_WORDS:
            out.append(_FIELD_WORDS[w])
    return "".join(out)


# 同一字段的多种中文说法：词素翻译只能给出一个名字（location→"位置"），
# 用户嘴里常是另一个（"所在地""地址"）。少数常用字段补上口语同义词。
_FIELD_CN_EXTRA = {
    "location": ("所在地", "地址", "国家", "地区"),
    "workshop": ("厂房", "工厂"),
    "zone": ("厂区", "分区"),
    "storage": ("存储条件", "储存条件"),
    "supplier": ("厂商", "供货商"),
    "customer": ("客户名", "客户名称"),
    # category 的词素翻译是"类别"，用户嘴里更常是"品类/分类/种类"（chem 实测）
    "category": ("品类", "分类", "种类"),
    "borrowed": ("借阅量", "借阅次数", "借出量"),
    "puritypct": ("纯度", "纯度值", "含量"),
    "capacityl": ("容量", "产能"),
    # 字段名是复合词，用户只问后半截："EQ007 的类型是什么"（字段 deviceType）、
    # "E1 在哪个产线"（对象属性 equipment_line_id）。
    "devicetype": ("类型", "设备类别", "设备分类"),
    "producttype": ("类型", "产品类别"),
    "equipmentlineid": ("所属产线", "所在产线"),
}


def _field_cn_names(field):
    """字段名 → 可能的中文说法列表（词素翻译 + 口语同义词），长的在前。"""
    cn = _cn_of_field(field)
    names = [cn] if cn else []
    key = re.sub(r"[_\-]", "", str(field or "")).lower()
    for syn in _FIELD_CN_EXTRA.get(key, ()):  # 补充说法
        if syn not in names:
            names.append(syn)
    return sorted(names, key=len, reverse=True)

# 未命中哨兵/关键词表的单一真相源(B3 收口): 不再在本文件写字面量。
from qa_constants import MISS_ANSWER, MISS_MARKERS


# ------------------------------------------------------------------ 词典加载

def load_dict(path, industry=None):
    with open(path, encoding="utf-8") as f:
        D = json.load(f)
    # 合并公共工业词典（基础层 + 行业层）：KB 覆盖公共，公共兜底 KB。
    # industry 缺省时按 lexicon 文件名解析 kb → 行业，使行业层真正被问答消费。
    try:
        from industrial_dict_loader import merge_industrial_dict, industry_for_kb
        if industry is None:
            base = os.path.basename(path or "")
            kb = ""
            if base.startswith("lexicon_") and base.endswith(".json"):
                kb = base[len("lexicon_"):-len(".json")]
            industry = industry_for_kb(kb)
        D = merge_industrial_dict(D, industry=industry)
    except Exception:
        pass  # 公共层缺失/损坏时降级为纯 KB 词典，不影响原功能
    return D


# ------------------------------------------------------------------ 解析(复用)

def parse_nt(nt_file):
    """解析 N-Triples。返回 (s,p,o) 列表。"""
    triples = []
    with open(nt_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("@"):
                continue
            m = re.match(r'^<([^>]*)>\s+<([^>]*)>\s+(.+?)\s*\.\s*$', line)
            if not m:
                continue
            s, p = m.group(1), m.group(2)
            o_raw = m.group(3).strip()
            om = re.match(r'^<([^>]*)>$', o_raw)
            if om:
                o = om.group(1)
            else:
                lm = re.match(r'^"((?:[^"\\]|\\.)*)"(?:\^\^<([^>]*)>)?', o_raw)
                o = lm.group(1) if lm else o_raw
            triples.append((s, p, o))
    return triples


def build_data(triples, dict_data):
    """三元组 -> {实例名: {属性: 值}}。字段名用尾部局部名。"""
    def tail(uri):
        return uri.split("#")[-1] if "#" in uri else uri.split("/")[-1]

    # 自动发现实体类：多表建模含多个类。类名是 "X type owl:Class" 的 subject
    classes = {tail(s) for s, p, o in triples
               if tail(p) == "type" and tail(o) == "Class"}
    if not classes:
        return {}
    individuals = set()
    for s, p, o in triples:
        if tail(p) == "type" and tail(o) in classes:
            individuals.add(s)
    # data 的键顺序必须确定：individuals 是 set，遍历顺序随 PYTHONHASHSEED 变，
    # 而下游有一批"取第一个/取最大"的选择（类名消歧、_class_prefixes 的插入序）。
    # 排序一次，换来同一份本体在任何进程里答案一致。
    data = {tail(i): {} for i in sorted(individuals)}
    for s, p, o in triples:
        sn = tail(s)
        # 只跳过 RDF 命名空间的 rdf:type，保留本体数据属性 type（产品类型等）
        if sn in data and not p.rstrip(">").endswith("rdf-syntax-ns#type"):
            data[sn][tail(p)] = o
    # 类的中文 label → 类名。词典里的 entity_cn2en 是建库时推断的，会串库
    # （chem 写成 mach_products）、会写成中文占位（"原料"→"原料"）。
    # 本体自己带着每个类的中文 label，这是最可靠的一条映射，存进该库的词典对象里，
    # 跟着 D 走，不跨库串台。
    if isinstance(dict_data, dict):
        _labels = {}
        _plabels = {}
        for s, p, o in triples:
            if tail(p) == "label":
                _sn = tail(s)
                _lv = str(o).split("^^")[0].strip().strip('"')
                if not _lv:
                    continue
                if _sn in classes:
                    _labels[_lv] = _sn
                else:
                    # 属性的中文 label：本体建库时写进来的，比词典 attr_cn2en 更可靠。
                    # 词典里常常缺"设备类型""车间""出版社"这类词，缺了就答成实例罗列。
                    _plabels[_lv] = _sn
        dict_data["_class_labels"] = _labels
        dict_data["_prop_labels"] = _plabels
        # 对象属性的 domain / range / 中文名。
        # 有了这三项，"Q001 检测的是哪个批次"不必依赖问题里写没写字段名：
        # 源实例属于 Valve_qc，问的是"批次"(Valve_batches)，本体里 domain=Valve_qc、
        # range=Valve_batches 的那条对象属性就是 checkedBy。
        _objprops = {tail(s) for s, p, o in triples
                     if tail(p) == "type" and tail(o) == "ObjectProperty"}
        _rel_schema = {}
        for s, p, o in triples:
            sn = tail(s)
            if sn not in _objprops:
                continue
            pn = tail(p)
            if pn in ("domain", "range"):
                _rel_schema.setdefault(sn, {})[pn] = tail(o)
            elif pn == "label":
                _rel_schema.setdefault(sn, {})["label"] = str(o).split("^^")[0].strip().strip('"')
        dict_data["_rel_schema"] = _rel_schema
        # 对象属性的**多值**边。build_data 的 data[s][p]=o 是赋值，一个主语同一关系
        # 有多条边时只留最后一条（B001 用了 R005、R007，压平后只剩 R007）。
        # 数据属性一行只有一个值，不受影响；关系边单独存一份多值，不动 data 结构。
        _rel_multi = {}
        for s, p, o in triples:
            sn, pn = tail(s), tail(p)
            if sn in data and pn in _objprops:
                tgt = tail(o)
                if tgt and tgt not in _rel_multi.setdefault((sn, pn), []):
                    _rel_multi[(sn, pn)].append(tgt)
        dict_data["_rel_multi"] = _rel_multi
        # 本体里**实际存在的属性名**（所有实例 dict 键的并集）。
        # 为什么需要：_find_attr 的字段反译只从词典的 attr_en2cn / field_aliases 里取
        # 候选字段，词典漏收的列就永远解析不出中文名 —— food 的设备表没有"车间"
        # 这一条，manufacturing 的供应商表没有"所在地"，问句"设备的车间有哪些"
        # 于是退化成一串设备名（实测误答）。本体里字段明明在，只是词典没登记。
        _all_fields = set()
        for _rec in data.values():
            if isinstance(_rec, dict):
                _all_fields.update(_rec.keys())
        dict_data["_all_fields"] = sorted(_all_fields)
    return data


# ------------------------------------------------------------------ 通用工具

def _field(rec, canonical, aliases):
    """按字段别名取标准字段值。canonical 如 status/deviceType/deviceName/location。
    别名找不到时，兜底匹配 csv_to_owl 驼峰化后的字段名(pump_status→pumpStatus)。
    再兜底做大小写不敏感的蛇形/驼峰归一化匹配(expiry_days↔expiryDays)。"""
    aliases = aliases or {}   # None 安全：调用方传过 None，否则 None.get 抛异常被吞成"有 0 台"
    # 也要兜空列表：词典里 field_aliases['status'] 可能是 []（valve 实测如此），
    # .get(k, default) 对"存在但为空"不生效 → 循环一次不进 → 取空 → 状态过滤答 0。
    for alias in (aliases.get(canonical) or [canonical]):
        if alias in rec:
            return rec[alias]
    # 驼峰兜底: 对每个别名尝试驼峰化
    for alias in (aliases.get(canonical) or [canonical]):
        parts = [p for p in alias.replace("-", "_").split("_") if p]
        camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
        if camel in rec:
            return rec[camel]
    # 大小写不敏感归一化兜底: 字段名去下划线+小写后匹配(expiry_days↔expiryDays)
    canon_norm = canonical.replace("_", "").lower()
    rn = {k.replace("_", "").lower(): v for k, v in rec.items()}
    if canon_norm in rn:
        return rn[canon_norm]
    # 唯一前缀兜底：内置别名把"功率"映射成泛化字段 power，而实际列是 powerKw。
    # 只在"本记录里恰好有一个字段以它开头"时采用，且要求长度≥4，
    # 避免 status→statusCode 这类多义前缀被误配。
    if len(canon_norm) >= 4:
        pref = [k for k in rec if k.replace("_", "").lower().startswith(canon_norm)]
        if len(pref) == 1:
            return rec[pref[0]]
    return rec.get(canonical, "")


def _type_matches(d, ty_en, aliases):
    """记录 d 是否属于类型 ty_en（值级判定）。

    优先规范字段(deviceType/type/category)精确匹配；未命中时兜底扫描全部字段——
    精确匹配优先，其次"取值包含"（容忍 304不锈钢 vs 不锈钢）。这使"XX类的产品"查询
    不再依赖字段名必须是 type/category（material/pressure_grade/设备类型等兜底字段也能命中），
    配合 lexicon_agent 自动把低基数枚举兜底进 type_cn2en，实现甲方自助补全词典。
    """
    for f in ("deviceType", "category", "type", "kind"):
        if _field(d, f, aliases) == ty_en:
            return True
    if str(d.get("deviceType", "") or d.get("category", "") or d.get("type", "")) == ty_en:
        return True
    for v in d.values():
        if str(v) == ty_en:
            return True
    if ty_en:
        for v in d.values():
            if ty_en in str(v):
                return True
    return False


def _status_matches(d, st_en, aliases, dict_data=None):
    """记录 d 的状态是否等于 st_en（值级判定，跨行业泛化增强）。

    数据里状态可能存中文（运行中/待机）或英文（running/idle），而 _find_enum 解析出的
    st_en 可能是英文规范值（running，来自 _COMMON_ZH_STATUS 兜底）或中文取值。
    这里先按规范字段精确匹配，未命中再用状态词典（status_cn2en / _COMMON_ZH_STATUS）
    把数据取值归一化到英文规范后与 st_en 比较，保证中文数据+英文兜底词也能命中。
    返回 True/False。
    """
    val = _field(d, "status", aliases)
    if not val:
        return False
    val_s = str(val).strip()
    if val_s == st_en:
        return True
    # 数据取值为中文：用内置状态词典(中文->英文规范)归一化再比。
    # 注意：status_cn2en 可能是恒等映射(运行中->运行中)，会覆盖英文规范，
    # 故英文归一化优先用内置 _COMMON_ZH_STATUS，status_cn2en 仅作补充。
    from lexicon import get_common_zh_status
    _zh_status = dict(get_common_zh_status())
    # 数据自定义状态词补充进归一化表（但不覆盖内置规范词）
    for _c, _e in (dict_data.get("status_cn2en", {}) or {}).items():
        _zh_status.setdefault(_c, _e)
    _canon = _zh_status.get(val_s)  # 中文值 -> 英文规范
    if _canon and _canon == st_en:
        return True
    # st_en 若是中文取值，反向归一化比较
    _rev = {e: c for c, e in _zh_status.items() if c != e}
    if st_en in _rev and val_s == _rev[st_en]:
        return True
    # st_en 本身是中文词、数据里存的却是英文（manufacturing 的 status_cn2en 是
    # 恒等映射 '运行中'→'运行中'，_find_enum 就把它原样当成了 st_en）。
    # 此时用归一化表把 st_en 也翻成英文规范值再比，否则"有多少台运行中的设备"答 0。
    _canon_st = _zh_status.get(str(st_en))
    if _canon_st and str(_canon_st).lower() == val_s.lower():
        return True
    # 英文值之间大小写不敏感比较
    if val_s.lower() == str(st_en).lower():
        return True
    return False


# 常见数值/极值字段的中文别名 -> 规范英文字段名（跨行业泛化，非硬编码具体行业）。
# 词典 attr_cn2en 缺失时兜底，覆盖高频中文口语：保质期/质保/寿命等。
# 数据来源 lexicon.get_attr_cn_aliases()（与原 _ATTR_CN_ALIASES 键值逐一一致）。
_ATTR_CN_ALIASES = get_attr_cn_aliases()


# 通用极值词 -> 目标数值字段中文关键词（跨行业泛化，非硬编码具体行业）。
# 极值模板"最X的Y"中 X 为形容词(贵/便宜/大/小/高/低)时，据此从 numeric_fields
# 推断 Y 对应的数值字段：贵/便宜→价格类，大/小→容量/吨位类，高/低→温度/功率/压力类。
_EXTREME_WORD_FIELDS = {
    "贵":   ["价格", "售价", "金额", "单价", "价钱", "成本", "投资"],
    "便宜": ["价格", "售价", "金额", "单价", "价钱", "成本", "投资"],
    "大":   ["容量", "尺寸", "吨位", "数量", "库存", "面积", "体积", "规模"],
    "小":   ["容量", "尺寸", "吨位", "数量", "库存", "面积", "体积", "规模"],
    "高":   ["温度", "价格", "功率", "压力", "高度", "转速", "电流", "电压", "水位", "速度"],
    "低":   ["温度", "价格", "功率", "压力", "高度", "转速", "电流", "电压", "水位", "速度"],
}


def _extreme_field(dict_data, q):
    """'最X的Y' 中 X 为通用极值词时，推断 Y 对应数值字段。
    从 numeric_fields(中文→字段) + attr_cn2en 中挑中文名含目标关键词的字段；
    按关键词长度降序取首个，保证"价格"先于"金额"等。返回 (en字段, 中文名) 或 (None,None)。"""
    m = re.search(r"最([贵便宜大小高低]+)[的]?", q)
    if not m:
        return None, None
    kws = _EXTREME_WORD_FIELDS.get(m.group(1))
    if not kws:
        return None, None
    cand = dict(dict_data.get("numeric_fields", {}) or {})
    cand.update({cn: en for cn, en in dict_data.get("attr_cn2en", {}).items()})
    # 反向守卫(批 2 修编造): "页数最大"/"借阅量最大" 这类问句点名的属性词并不在词典里,
    # 泛化极值推断会把"最大"硬猜成某个含"大/高"类关键词的数值字段(如 库存),
    # 产出编造答案("最大库存")。仅当"最"前的中文词尾部能对上已知属性/数值字段名时
    # 才允许推断, 否则不猜(交上层如实拒答)。
    _pre = re.search(r"([\u4e00-\u9fff]{1,6})最", q)
    if _pre:
        _word = _pre.group(1)
        if not any(_word[-L:] in cand for L in range(1, min(5, len(_word)) + 1)):
            return None, None
    for kw in sorted(kws, key=len, reverse=True):
        for cn in sorted(cand, key=len, reverse=True):
            if kw in cn:
                return cand[cn], cn
    return None, None


# CJK 统一表意文字范围 [U+4E00, U+9FFF], 用 chr() 构造避免源码转义被改写
_CJK_START, _CJK_END = chr(0x4E00), chr(0x9FFF)


def _strip_unit(cn):
    """去掉属性中文名末尾的单位后缀, 让口语能命中带单位后缀的词典键。

    支持括号单位('功率（千瓦）'→'功率')与无括号单位('功率kW'/'温度℃'/'尺寸mm'→
    '功率'/'温度'/'尺寸'), 从而 '功率最大的设备' 能命中词典键 '功率kW'→powerKw。
    仅当剥离后仍以中文开头才生效, 避免把纯英文字段名('batchsizel')剥空造成误匹配。
    """
    if not cn:
        return cn or ""
    stripped = re.sub(r"[（(][^（）()]*[）)]$", "", cn).strip()
    base = re.sub(r"[^%s-%s]+$" % (_CJK_START, _CJK_END), "", stripped)  # 去末尾非中文单位
    if _CJK_START <= base[:1] <= _CJK_END:
        return base.strip()
    return stripped  # 无中文则保留原样, 防误剥纯英文键


def _attr_val(rec, attr_en, aliases=None, data=None):
    """取属性值，兜三种真实形态（枚举/过滤/极值 共用）：
      ① 直接字段名（与词典 attr_cn2en 一致）
      ② camelCase —— 建图把 device_type 规范化为 deviceType，只按 snake_case 取会全空
         （这是"多词属性全线失效"的根因）
      ③ 对象属性 —— **关系表**的外键被正确建成 ObjectProperty，键名形如
         valveBatchIngredientRawId，值是目标实例的引用（不是字面量）。
         此时要剥成用户认的 ID（…/valve#Valve_raw_materials_R009 → R009）。
    取不到返回 None。
    """
    if rec is None:
        return None

    def _clean(v):
        """把"指向另一条记录的引用"剥成 ID。
        判据优先用**图结构**：值是不是本体里的实例名（在 data.keys() 里）——
        是就取末段。这比按形状猜稳：主键叫 EQ-1001、2024-001 或中文都认得出。
        没有 data 时退化为形状判定（旧行为，兼容单独调用的场景）。
        """
        s = str(v).strip().rstrip("/")
        if not s:
            return None
        if "#" in s or "://" in s:
            s = s.split("#")[-1].split("/")[-1]
        if data is not None:
            if s in data and "_" in s:
                return s.rsplit("_", 1)[-1] or s
            return s or None
        _tail = s.rsplit("_", 1)[-1] if "_" in s else ""
        if re.match(r"^[A-Za-z]{1,3}\d+$", _tail):
            s = _tail
        return s or None

    v = _field(rec, attr_en, aliases or {})
    if v not in (None, ""):
        return _clean(v)
    _camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), attr_en)
    v = rec.get(_camel)
    if v not in (None, ""):
        return _clean(v)
    if attr_en == "id":
        # "id" 后缀太短，endswith 会误命中 *_product_id / *_raw_id
        # （"编号"于是取到了"产品编号"的值）。主键交给"实例名末段"那条路，
        # 这里直接不再猜。
        return None
    _suffix = attr_en.replace("_", "").lower()
    for k, vv in rec.items():
        if not isinstance(vv, str):
            continue
        kl = str(k).lower()
        # 归一化后比：键名可能带表前缀且用 snake_case（orders_customer_id），
        # 直接用 endswith("customerid") 对不上，去下划线后才相等。
        if kl.replace("_", "").endswith(_suffix) or kl.endswith(_suffix + "id"):
            return _clean(vv)
    return None


def _find_attr(dict_data, q):
    """从词典找问题里出现的属性中文词 -> 字段英文。按长度降序避免短词短路。

    兼容词典键带单位后缀('功率（千瓦）')也能被'功率'口语命中: 为每个 attr_cn2en 键
    额外生成去单位后缀的变体, 保证映射到可解析的规范字段(power_kw), 而非泛化别名
    (如'功率'→'power')。词典未命中时兜底 _ATTR_CN_ALIASES / numeric_fields / 极值推断。
    """
    cand = []  # (min_len, 中文匹配词, 英文字段); min_len 防单字别名误命中
    # 本体自带属性中文 label 优先：建库时写进本体的，跟着这份数据走，不会串库。
    # 词典 attr_cn2en 常缺词（"设备类型""车间""出版社"），缺了就落到"列出实例"，
    # 用户问类型、拿到一串设备名。先把本体里的 label 用掉。
    _ent_names0 = set(dict_data.get("entity_cn2en", {}) or {})
    for cn, en in sorted((dict_data.get("_prop_labels") or {}).items(),
                         key=lambda x: len(x[0]), reverse=True):
        if cn and len(cn) >= 2 and cn not in _ent_names0 and cn in q:
            return en, cn
    # 词典里可能混入实体名（valve 的 attr_cn2en 就同时收了 '原料' 和 '库存'），
    # 于是"原料中库存最大是多少"里同长的"原料"按插入序先命中，属性被解析成 raw_parts，
    # 极值/聚合整条链随之失效。实体名不是属性，这里先剔除。
    _ent_names = set(dict_data.get("entity_cn2en", {}) or {})
    for cn, en in dict_data.get("attr_cn2en", {}).items():
        if cn in _ent_names:
            continue
        cand.append((1, cn, en))
        base = _strip_unit(cn)
        if base and base != cn:
            cand.append((1, base, en))
    # 字段名反译：词典没给中文名的字段，把英文列名翻回中文再参与匹配。
    # 现场：food 的设备表整张没有中文名，library 的 publisher 中文名就是 "publisher"，
    # 于是"设备的设备类型有哪些""图书的出版社有多少种"找不到字段，退化成罗列实例名。
    _fields = set()
    for _en in (dict_data.get("attr_en2cn") or {}):
        _fields.add(_en)
    # field_aliases 的键是规范字段名（deviceType/location），值才是实际列名（category）。
    # 两边都要收：键给出"设备类型"，值给出真实列名。
    for _k, _v in (dict_data.get("field_aliases") or {}).items():
        if isinstance(_k, str):
            _fields.add(_k)
        for _x in (_v if isinstance(_v, list) else [_v]):
            if isinstance(_x, str):
                _fields.add(_x)
    for _en in (dict_data.get("numeric_fields") or {}).values():
        if isinstance(_en, str):
            _fields.add(_en)
    # 本体里真实存在的字段（见 build_data 的 _all_fields）。词典漏登记时靠它兜底。
    for _en in (dict_data.get("_all_fields") or []):
        _fields.add(_en)
    for _en in sorted(_fields, key=len, reverse=True):
        # 对象属性（hasCategory / hasBatch…）是关系边，不是字面字段：
        # 它和真正的 category 会被反译成同一个中文（"类别"），而名字更长，
        # 排序后先命中 → 「图书的类别有哪些」答成一串
        # Library_inventoryCategory_历史 这样的 URI。字面量明明就在 category 里。
        if re.match(r"^has[A-Z]", str(_en)):
            continue
        for _cn in _field_cn_names(_en):
            if len(_cn) >= 2 and _cn not in _ent_names and _cn in q:
                cand.append((2, _cn, _en))
    for cn, en in _ATTR_CN_ALIASES.items():
        cand.append((2, cn, en))
    nf = dict_data.get("numeric_fields", {}) or {}
    for cn, en in nf.items():
        cand.append((1, cn, en))
    seen = set()
    # 按长度降序, 优先长词(完整键), 稳定排序保证同长时 attr_cn2en 变体先于泛化别名
    for min_len, cn, en in sorted(cand, key=lambda x: len(x[1]), reverse=True):
        key = (cn, en)
        if key in seen:
            continue
        seen.add(key)
        if len(cn) >= min_len and cn in q:
            return en, cn
    # 通用极值词兜底: '最X的Y' 中 X 为形容词(贵/便宜/大/小/高/低)时推断 Y 的数值字段
    return _extreme_field(dict_data, q)


def _num(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _display_name(rec, aliases, default=""):
    """显示名：优先 deviceName；无名称字段时兜底 id/UDI/主键，最后用 key。"""
    n = _field(rec, "deviceName", aliases)
    if n:
        return n
    # 兜底: 尝试常见主键字段 (含驼峰)
    for key in ("id", "udi", "UDI", "device_id", "serial_no", "code", "name",
                "product_name", "productName", "raw_name", "rawName",
                "unit_name", "unitName", "equipment_name", "equipmentName",
                "line_name", "lineName", "team_name", "teamName",
                "section_name", "sectionName", "batch_no", "batchNo",
                "title", "label"):
        if key in rec:
            return rec[key]
    # 任意 *Name 字段兜底：名字清单没法穷举，library 的 bookName、manufacturing 的
    # supplierName / lineName 都不在上面那条表里，取不到就返回空串 ——
    # 极值回答于是变成"最大借阅量的记录:  (借阅量=10.0)"，主语是空的。
    for k, v in rec.items():
        if str(k).lower().endswith("name") and v not in (None, ""):
            return v
    return default


# 内置中文状态词 → 英文值兜底（当词典缺失中文映射时，覆盖高频运维词汇）。
# 数据来源 lexicon.get_common_zh_status()（与原 _COMMON_ZH_STATUS 键值逐一一致）。
_COMMON_ZH_STATUS = get_common_zh_status()


def _find_enum(dict_data, q, which, data=None):
    """从词典找问题里出现的枚举词 -> 值。which in (status/type/zone)。
    增强：status 词在词典缺中文映射时，用内置中文→英文兜底。
    增强：LLM 语义聚类 synonym_map 展开——问题含同义词/别名时命中规范词（乳制品→奶制品）。

    🔴 2026-09-15 修（源头）：跳过"同时是实体名"的候选。
    问句几乎必然含实体名（"设备的…"），而 type_cn2en 里混进了实体名（"设备"→"equipment"），
    被这里当类型取值返回 → 下游按"类型值=设备"过滤 → 恒空 → 错答"有 0 台设备"/"无设备"。
    这是"枚举/极值/平均三分支齐崩"的共同病因，在此一处修全部。
    """
    key = f"{which}_cn2en"
    _ents = {str(k) for k in (dict_data.get("entity_cn2en") or {})}
    for cn, en in sorted(dict_data.get(key, {}).items(), key=lambda x: len(x[0]), reverse=True):
        if cn in q:
            if cn in _ents:      # 实体名不是类型取值 → 跳过
                continue
            return en, cn
    # 同义词展开：LLM 语义聚类 synonym_map 反查——问题含某词组任一同义/别名时，映射回规范词对应的枚举值
    smap = dict_data.get("synonym_map", {}) or {}
    if smap:
        en_map = dict_data.get(key, {})
        reverse = {}  # 同义词/别名 -> 枚举规范词
        for canon, group in smap.items():
            for m in [canon] + list(group):
                if m:
                    reverse.setdefault(m, canon)
        for term in sorted(reverse, key=len, reverse=True):
            if term and term in q:
                canon = reverse[term]
                # 🔴 实体名同样要剔除：synonym_map 一旦把状态词并进实体词组
                # （valve 的 smap 里 '运行中' 的 canon 竟是 '设备'），
                # "运行中的设备有多少台" 会返回 type=('equipment','设备')，
                # 进而抢走"状态+类型"组合分支 → 答"有 0 台运行中的设备"。
                if canon in en_map and canon not in _ents:
                    return en_map[canon], canon
                # 规范词不在枚举键中，但同义词可能直接等于某枚举键。
                # 🔴 候选词必须出现在问题里：synonym_map 一旦混进实体名（valve 的 smap
                # 有 '设备' 键、其组内含 '运行中'），"设备有多少台" 就会返回
                # ('运行中','运行中') —— 候选压根不在问句里，下游过滤必然全空。
                for ck in en_map:
                    if ck in q and ck not in _ents and ck in [canon] + list(smap.get(canon, [])):
                        return en_map[ck], ck
    # status 兜底：中文运维词 → 英文值（词典可能只有英文键，如 {running:running}）
    if which == "status":
        for cn, en in sorted(_COMMON_ZH_STATUS.items(), key=lambda x: len(x[0]), reverse=True):
            if cn in q:
                return en, cn
        # 最后一道：问句里直接写了数据中的状态原值（"idle的设备有哪些"）。
        # 词典 status_cn2en 是中文→英文（待机→idle），用户照着台账问英文时
        # 中文键一个都不在问句里，_find_enum 返回空 → 整句拒答。
        if data:
            _vals = set()
            for _d in data.values():
                _v = str(_field(_d, "status", {}) or "").strip()
                if _v and 2 <= len(_v) <= 12:
                    _vals.add(_v)
            _ql = q.lower()
            for _v in sorted(_vals, key=len, reverse=True):
                if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(_v.lower()), _ql):
                    return _v, _v
    return None, None


def _extract_nums(q):
    return [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)', q)]


def _fmt_names(names, limit=20):
    shown = names[:limit]
    out = "\n".join("  - " + n for n in shown)
    if len(names) > limit:
        out += f"\n  ... 共 {len(names)} 条"
    return out


def _value_filter_count(q, data, with_names=False):
    """属性值过滤计数（泛化）：形如"XX的数量/多少"——问题含某属性的取值(或其公共子串)时统计符合行。
    - 提取问题核心片段(去掉 数量/多少/共/总 等量词)，取其 2~3 字公共子串作为过滤指纹；
    - 对每个记录的各文本取值，若与指纹有>=2字公共子串则视为该字段命中；
    - 跨多个字段命中(如材质+名称：不锈钢波纹管)按 AND 取交集返回；单字段命中按该字段计数。
    data profiling 未覆盖的复合过滤（材质/名称/类型取值）走此兜底，命中即返回，避免"暂不支持"。
    返回 (count, 描述) 或 None(无命中)；with_names=True 时返回 (count, 描述, [命中实体名])，
    供 evidence.build_trace 产出可回溯证据(批 2)。"""
    core = re.sub(r'(的数量|有多少个|有多少|多少个|共有多少|共多少|数量|多少|总数|共|总)', '', q)
    core = core.strip().replace(" ", "")
    if len(core) < 2 or not re.search(r'(数量|多少)', q):
        return None
    grams = set()
    for i in range(len(core)):
        for L in (2, 3):
            if i + L <= len(core):
                grams.add(core[i:i + L])
    if not grams:
        return None
    records = []  # (name, 命中的字段集合)
    for n, d in data.items():
        fields = set()
        for k, v in d.items():
            vs = str(v)
            if len(vs) < 2 or _num(vs) is not None:
                continue
            if any(g in vs for g in grams):
                fields.add(k)
        records.append((n, fields))
    strong = [(n, f) for n, f in records if len(f) >= 2]
    weak = [(n, f) for n, f in records if len(f) == 1]
    if strong:
        return (len(strong), "多字段(材质+名称等)", [n for n, _ in strong]) if with_names \
            else (len(strong), "多字段(材质+名称等)")
    if weak:
        return (len(weak), "单字段取值", [n for n, _ in weak]) if with_names \
            else (len(weak), "单字段取值")
    return None


# ------------------------------------------------------------------ 问答主逻辑

_EXTREME = re.compile(r"最(大|高|多|低|小|少|长|短|贵|便宜|快|慢|久|重|轻|新|老|早|晚|近)")
_EXTREME_MAX = ("最大","最高","最多","最长","最贵","最快","最久","最重","最新","最老")
_EXTREME_MIN = ("最小","最低","最少","最短","最慢","最轻","最早","最便宜")


def _is_max(q):
    """问题是否求极值中的最大值。MIN 词(最便宜/最短/最慢等)优先按最小。"""
    if _is_min(q):
        return False
    for k in _EXTREME_MAX:
        if k in q:
            return True
    # 含"最"但未列出的, 按常识多数求最大
    return _EXTREME.search(q) is not None


def _is_min(q):
    return any(k in q for k in _EXTREME_MIN)


# ------------------------------------------------------------------ 通用跨域校验
# 数据查询意图正则(计数/列表/极值/范围/统计等)。跨域校验只拦"明确的数据查询",
# 不拦开放式/咨询问题(那些走 LLM 兜底生成建议)。
_DATA_QUERY_RE = re.compile(
    r"多少|数量|总数|几个|总共|共计|共有|一共有|列出|有哪些|哪些|"
    r"最[大小高低多少长短贵便宜快慢久重轻新老早晚近]|共\s*多少|"
    r"是多少|等于|大于|小于|高于|低于|平均|合计|总和|统计|分组|"
    r"台|条|个|张|艘|本|支|卷|份")

# 通用查询意图词(计数/列举/极值/统计的语法标记): 从 _DATA_QUERY_RE 抽出的 ≥2 字中文词
# 加上单位量词。它们是数据查询的"意图", 不是领域概念, 且几乎每个 kb 的字段名里都可能出现
# (如 valve 的 numeric_fields 就有"数量")。跨域域内判定须排除它们, 否则跨域问题
# ("冲床的数量"命中通用字段名"数量")会被误判为域内而绕过拦截。
_DATA_INTENT_WORDS = {
    w for w in re.findall(r"[\u4e00-\u9fff]{2,}", _DATA_QUERY_RE.pattern)
} | set("个张条台艘本支卷份")


def kb_vocab(D):
    """该 kb 本体的全部领域词(中文实体/类型/状态/区域/属性/数值字段等)。

    跨域判定依据: 词典 entity_cn2en(实体类) + type/status/zone/category 枚举 +
    attr_cn2en/attr_en2cn(属性) + numeric_fields。只收集该 kb 词典里真实出现的概念,
    不依赖任何白名单词表 —— 换任何行业/数据源都通用。
    """
    voc = set()
    for key in ("entity_cn2en", "type_cn2en", "status_cn2en", "zone_cn2en",
                "category_cn2en", "attr_cn2en", "attr_en2cn"):
        m = (D or {}).get(key) or {}
        for k in m:
            if k:
                voc.add(k)
            v = m[k]
            if isinstance(v, str) and v:
                voc.add(v)
    for k in ((D or {}).get("numeric_fields") or {}):
        if k:
            voc.add(k)
    return {w for w in voc if w and len(str(w)) >= 1}


def is_cross_domain_data_query(q, D):
    """通用跨域校验(取代原白名单词表钩子)。

    若问题是一条"明确的数据查询"(计数/列表/极值/范围/统计), 且其中引用的实体概念
    不在该 kb 本体任何实体类/词典(即问题里不含任何该 kb 领域词), 判定为跨域 →
    应禁止 LLM 兜底编造, 强制返回"无相关数据"。
    横向覆盖所有跨域问题(问书/船/测线/冲床/图纸…), 不靠具体词表。
    非数据查询(开放式/咨询)返回 False, 不拦截。

    域内判定排除"通用查询意图词"(数量/多少/几个/哪些…): 这些词是数据查询的语法
    意图标记, 且几乎每个 kb 的字段名里都会出现(如 valve 的 numeric_fields 有"数量")。
    若把它们纳入域内判定, 跨域问题(如"冲床的数量")会因命中通用字段名"数量"被误判为
    域内, 绕过跨域拦截 → 空答案/编造数字假命中。域内判定只看"领域区分词"
    (实体类/类型/状态/区域等), 才能正确识别真·本域问题。
    """
    if not q or not D:
        return False
    if not _DATA_QUERY_RE.search(q):
        return False  # 非数据查询, 不拦
    voc = kb_vocab(D)
    if not voc:
        return False
    for w in voc:
        if w and w not in _DATA_INTENT_WORDS and w in q:
            return False  # 命中该 kb 领域区分词 → 域内, 不拦
    return True


def _stem_en(word: str) -> str:
    """英文词形归一（只用于把词典表名和实际类名对上，不改原值）。

    以前 multi_table 用 `w[:-1]` 去复数，batches→batche、classes→classe，
    于是 qc.batch_id 匹配不上 batches 表，外键退化成字符串属性。
    这里按英语规则处理 -ies/-ses/-xes/-ches/-shes/-s。
    """
    w = str(word or "").strip().lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _class_prefixes(data):
    """从实例 key 反推本库真实的类名前缀 {小写类名: 实例数}。

    build_data 的 key 形如 `Chem_products_P001` / `Valve_qc_Q001`。
    去掉最后一段（实例号）得到类名。实例号自带下划线时仍以真实类名为前缀，
    下游统一用 `前缀 in key.lower()` 判定，不受影响。
    """
    out = {}
    for k in data:
        p = str(k).rsplit("_", 1)[0].lower()
        out[p] = out.get(p, 0) + 1
    return out


def _class_cn_aliases(cls_name, data, D):
    """一个实体类可能被用户叫成什么中文名（类名反译 + 代表字段反译）。

    为什么要这层：本体的类 label 不一定有中文 —— manufacturing 的 label 直接是
    `Line` / `Supplier`，library 的类叫 `Library_inventory`（装的却是书）。
    词典又常是通用模板（设备/产品/客户…），跟这份数据没关系，
    于是「有多少条产线」「有多少本图书」解析不到实体，一路掉到"暂不支持"。
    类名反译得「产线」「库存」，代表字段（bookName）反译得「图书」，两者都算别名。
    """
    names = []
    tail = str(cls_name or "").rsplit("_", 1)[-1]
    cn = _cn_of_field(tail)
    if len(cn) >= 2:
        names.append(cn)
    # 该类第一个实例的 *Name 字段：bookName → 图书名称 → 去掉"名称"剩「图书」
    for key, rec in data.items():
        if not str(key).lower().startswith(str(cls_name).lower()):
            continue
        if not isinstance(rec, dict):
            continue
        for f in rec:
            if not str(f).lower().endswith("name"):
                continue
            base = _cn_of_field(f)
            for suf in ("名称", "名字"):
                if base.endswith(suf):
                    base = base[: -len(suf)]
                    break
            if len(base) >= 2:
                names.append(base)
            break
        break
    return [n for n in names if len(n) >= 2]


def _resolve_entity_map(D, data):
    """中文实体名 → **本库真实存在的类名前缀**（词典值先验证，验不过再按词干回退）。

    病因（实测）：chem 库的 NT 类名是 `Chem_products`，而词典 entity_cn2en 里写的是
    `mach_products`（建词典时串了别的库）。问答按 `mach_products in key` 过滤，
    一条也匹配不上 → 「有多少个产品」答 0，而库里明明有 7 个。
    同一个错误值还被 _entity_subset / _agg_distribution 复用，导致枚举、分组一起崩。

    修法：先把词典值拿到本库的类名清单里核一遍；核不上就取末段词干比对
    （products↔product），仍核不上才保留原值（下游可能只是用它做展示）。
    """
    raw = dict(get_entity_cn2uri())
    raw.update(D.get("entity_cn2en", {}) or {}) if isinstance(D, dict) else None
    # 本体自带的中文类名优先：它是建库时从类 label 读出来的，不会串库。
    labels = (D.get("_class_labels") or {}) if isinstance(D, dict) else {}
    cls = _class_prefixes(data)
    out = {}
    for cn, uri in raw.items():
        if cn in labels and labels[cn].lower() in cls:
            out[cn] = labels[cn].lower()
            continue
        u = str(uri or "").strip().lower()
        if not u:
            continue
        # 先按"末段词干相等"找，再退回子串。顺序很重要：food 的批次表叫
        # food_batches（9 条），投料表叫 food_batch_ingredient（9 条），"batch"
        # 子串同时命中两者，取"实例多的那个"在平局时随手挑 —— 而 data 的键来自
        # set（individuals），遍历顺序随 PYTHONHASHSEED 变，于是**同一个问题在不同
        # 进程里一个答 9 一个答 24**。词干相等是语义正确的那个，且没有平局。
        stem = _stem_en(u.rsplit("_", 1)[-1])
        hit = [c for c in cls if _stem_en(str(c).rsplit("_", 1)[-1]) == stem]
        if not hit:
            hit = [c for c in cls if u in c]
        if hit:
            # 排序键必须全序：平局时按名字定，不能靠 dict 插入顺序。
            out[cn] = max(hit, key=lambda c: (cls[c], -len(c), c))
        else:
            out[cn] = u
    # 补充别名：label 没给中文、词典又没收的类，用类名/代表字段反译兜一层。
    # 只补不改 —— 已有映射（含词典验过的那条）优先，避免把"设备"换成别的类。
    for c in cls:
        for alias in _class_cn_aliases(c, data, D):
            # 别名可以**覆盖验证失败的旧值**：energy 库只有一个类 Energy_station，
            # 词典却把"设备"映射到 equipment（本库没有这个类）。deviceName 反译出的
            # "设备"才是对的。只补不改会把这个错值一直留着，"有多少台设备"永远拒答。
            if alias not in out or out[alias] not in cls:
                out[alias] = c
    return out


def _inst_rows(data, prefix):
    """按类名前缀取实例，**精确到"类"这一层**，不做子串匹配。

    现场（food 实测）：「批次总数」答 24，真值 9。因为 `food_batches`（9 条批次）
    与 `food_batch_ingredient`（15 条投料记录）都含子串 "batch"，子串匹配把两张表
    并成一张数了。前缀式判定（等于，或以 `前缀_` 开头）才分得开。

    前缀匹配一条都没有时退回子串匹配：有些库的实例名不带类前缀，
    宁可宽一点也不要直接返回空。
    """
    p = str(prefix or "").strip().lower()
    if not p:
        return []
    rows = [(k, d) for k, d in data.items()
            if str(k).lower() == p or str(k).lower().startswith(p + "_")]
    if rows:
        return rows
    return [(k, d) for k, d in data.items() if p in str(k).lower()]


def _entity_subset(q, D, data):
    """实体消歧: 若问题含 entity_cn2en 的实体词(如"机组"), 返回该实体类的实例子集;
    否则返回全部。解决"运行中的机组"误匹配到设备(锅炉/发电机)而非机组。

    实体名到类名的映射统一走 _resolve_entity_map（先验证，再回退），
    不再直接信词典里的表名 —— 词典串库时这里会把整库当子集返回。
    """
    emap = _resolve_entity_map(D, data)
    # 先认"X中/里"结构：中文"订单中客户编号为C003"里，X(订单)才是限定的表，
    # 而 Y(客户编号)只是要过滤的属性。若只按长度降序，同长时"客户"会按插入序抢赢
    # "订单"，选错了表 → 属性在该表取不到 → 答 0。
    for cn in sorted(emap, key=len, reverse=True):
        if cn and cn in q and re.search(re.escape(cn) + r"\s*(中|里|内的|之内)", q):
            uri_sub = emap[cn].lower()
            sub = dict(_inst_rows(data, uri_sub))
            if sub:
                return sub
    for cn in sorted(emap, key=len, reverse=True):
        if cn and cn in q:
            uri_sub = emap[cn].lower()
            sub = dict(_inst_rows(data, uri_sub))
            if sub:
                return sub
    return data


# ------------------------------------------------------------------ 聚合/分组查询
# '各X的Y' / 'XX分布' / '平均/合计' 等聚合查询统一落在规则引擎(确定性, 不依赖 LLM)。
# 分组属性词典驱动: attr_cn2en(车间->workshop/类型->type/状态->status) + 枚举别名兜底。

def _agg_field_value(d, en, aliases):
    """取某记录的分组/聚合字段值(en)。对类型/状态/区域类字段做多字段兜底解析,
    兼容 设备用 deviceType、产品用 type、区域用 workshop/location 等差异。"""
    if en in ("type", "deviceType", "category"):
        for f in ("deviceType", "category", "type", "kind"):
            v = _field(d, f, aliases)
            if v:
                return v
        return _field(d, en, aliases) or "未知"
    if en in ("status", "state", "result"):
        return _field(d, "status", aliases) or "未知"
    if en in ("workshop", "zone", "location", "region", "area"):
        for f in ("workshop", "zone", "location", "region", "area"):
            v = _field(d, f, aliases)
            if v:
                return v
        return _field(d, en, aliases) or "未知"
    return _field(d, en, aliases) or "未知"


def _enum_value_in_data(q, data, aliases):
    """问句里直接出现了某个类型值，而词典没收录它时，从数据本身认出来。

    现场："H类机器有多少台" —— type 列的值是 H/L/M，词典 type_cn2en 是空的，
    _find_enum 找不到，问句被当成"实体总数"，答 10000 台（全部机器）。
    这里改成看数据：类型列里哪个取值原样出现在问句里，就是它。
    纯数字取值不参与（"1" 会命中任何带数字的问句）。
    """
    cands = set()
    for d in data.values():
        for f in ("deviceType", "device_type", "type", "Type", "category", "kind"):
            v = _field(d, f, aliases)
            v = str(v or "").strip()
            if v and v != "未知" and len(v) <= 8 and not re.fullmatch(r"[\d.]+", v):
                cands.add(v)
    for v in sorted(cands, key=len, reverse=True):
        if v in q:
            return v, v
    return None, None


def _find_agg_attr(D, q):
    """聚合/分组属性检测(词典驱动, 不硬编码词)。
    返回 (en字段, 中文属性, 模式) 或 (None,None,None)。
    - 模式 "group": '各X' 分组计数 (各车间/各类型/各状态)
    - 模式 "dist" : 'X分布' 枚举分布 (状态分布/类型分布/车间分布)
    """
    ac2e = D.get("attr_cn2en", {}) or {}
    # 先找 '各X'(分组计数): 车间/类型/状态等
    for cn, en in sorted(ac2e.items(), key=lambda x: len(x[0]), reverse=True):
        if "各" + cn in q:
            return en, cn, "group"
    # 再找 'X分布'(枚举分布)
    for cn, en in sorted(ac2e.items(), key=lambda x: len(x[0]), reverse=True):
        if cn + "分布" in q:
            return en, cn, "dist"
    return None, None, None


def _agg_distribution(q, D, data, en):
    """统计某分组字段的分布。返回 (有序 dict {值:计数}, 该字段非空记录数)。

    实体作用域: 问题含实体词时用 _entity_subset 消歧(各车间设备/各类型产品只在该类聚合)。
    问题不含实体词时(裸"状态分布/类型分布"), 自动选该字段覆盖最广的实体类, 避免把
    多类不兼容枚举(设备的运行中 vs 批次的生产中)混在一起并产生大量"未知"。"""
    aliases = D.get("field_aliases", {})
    sub = _entity_subset(q, D, data)
    # 自动选类: 若问题没点名实体, 且存在多个实体类, 取该字段非空率最高的类
    _named = any(cn in q for cn in (D.get("entity_cn2en", {}) or {})) or \
             any(cn in q for cn in (get_entity_cn2uri() or {}))
    if not _named:
        # 用实体表名子串给每个实例归类(如 valve_equipment/valve_products), 而非取 key 首段,
        # 因为实例 key 形如 "Valve_equipment_E001", 首段 "Valve" 对所有类都相同。
        _cls_list = []
        for cn, _en in (D.get("entity_cn2en", {}) or {}).items():
            if isinstance(_en, str) and _en:
                _cls_list.append(_en.lower())
        _cls_list = sorted(set(_cls_list), key=len, reverse=True)
        if not _cls_list:
            _cls_list = [str(v).lower() for v in (get_entity_cn2uri() or {}).values() if v]
        classes = {}
        for k in sub:
            matched = next((c for c in _cls_list if c in k.lower()), None)
            classes.setdefault(matched or "_other", []).append(k)
        if len(classes) > 1:
            best_c, best_hits = None, -1
            for c, keys in classes.items():
                if c == "_other":
                    continue
                hits = sum(1 for k in keys if _agg_field_value(data[k], en, aliases) != "未知")
                if hits > best_hits:
                    best_c, best_hits = c, hits
            if best_c:
                sub = {k: d for k, d in sub.items() if best_c in k.lower()}
    cnt = defaultdict(int)
    nonempty = 0
    for d in sub.values():
        v = _agg_field_value(d, en, aliases)
        if v != "未知":
            nonempty += 1
        cnt[v] += 1
    # 去掉"未知"桶: 只展示真实取值, 避免大段噪音(未点名实体时尤其重要)
    cnt.pop("未知", None)
    return dict(sorted(cnt.items(), key=lambda x: (-x[1], str(x[0])))), nonempty


def _filter_value_in_q(q, ents, data, entity_cn):
    """枚举类问句里点名的属性值 —— "NorthAir 的设备有哪些" → "NorthAir"。

    取**最长**命中，排除疑问词与实体类中文名本身。返回 None 表示问句没点名值，
    调用方按原样枚举全部实例。

    🔴 为什么要这个函数：旧版「XX 的实体有哪些」直接枚举该实体类的**全部**实例再
       截断到 20 条，问句里点的值完全没用上。实测 AAS 台账
       「NorthAir 的设备有哪些」→ 返回前 20 台（真值 5 台，且多数不是 NorthAir）。
       数字读起来像正常回答，是最危险的一类错答。
    """
    stop = {"哪些", "什么", "所有", "全部", "列出", "一共", "设备", "未知", ""}
    cands = set()
    for e in ents[:300]:
        for v in (data.get(e) or {}).values():
            if isinstance(v, str):
                s = v.strip()
                if 2 <= len(s) <= 40:
                    cands.add(s)
    best = None
    for s in cands:
        if s in stop or s == entity_cn:
            continue
        if s in q and (best is None or len(s) > len(best)):
            best = s
    return best


def _instance_by_name_value(q, data, D):
    """问句里写的是实例的"名字"，不是编号（"供应商 Siemens 的所在地"）。

    台账里 Siemens 是 supplierName 这个**字段的值**，实例名却叫 Supplier_S1。
    按编号找永远找不到，于是整句拒答。这里在该实体类的实例里找"某个字段的值
    原样出现在问句里"的那一条，并且只在唯一命中时才认 —— 匹配到多条就不是指认，
    硬猜会答成另一个人的资料。
    """
    emap = _resolve_entity_map(D, data)
    cls_set = {str(c).lower() for c in _class_prefixes(data)}
    for cn in sorted(emap, key=len, reverse=True):
        c = str(emap[cn]).lower()
        if not (cn and _class_in_q(cn, q) and c in cls_set):
            continue
        hits = []
        for k, d in _inst_rows(data, c):
            if not isinstance(d, dict):
                continue
            for f, v in d.items():
                sv = str(v or "").strip()
                # 纯数值不参与指认："价格大于10.06"里 10.06 就是某条记录的价格，
                # 拿它当"点名"会答成"苹果汁的价格：10.0"。
                if (len(sv) >= 2 and not re.fullmatch(r"[\d.,%+\-]+", sv)
                        and sv in q):
                    hits.append(k)
                    break
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None  # 指认不唯一，不猜
    return None


def _device_attr_lookup(q, data, D, aliases):
    """M15-8：「某设备的某属性是多少」的直查。答不出就返回 None。

    症状来源：「AC-01 的投用年份是多少」被答成"符合条件共 319 条"——
    下游的取值过滤计数把 `AC-01` 当成一个"取值"去数有多少条记录带它。
    拦住那个兜底之后缺口才暴露：**这个引擎从来没有直查分支**，
    只有"哪些/列出"的属性取值枚举。所以这里补的是本来就缺的那个分支。

    🔴 三处与 AAS 引擎视图有关的不对称，改动时都要记住：
    ① 设备编号在键里是下划线（`AC_01`），问句里是连字符（`AC-01`）；
    ② **设备级记录**（`urn_ledger_AC_01_AssetData`）里没有属性值，
       属性值在**属性节点**（`AC_01_AssetData_InstallYear`）上，所以要双向定位；
    ③ 属性节点是「一属性一个实例」，值在 `value` 键上、列名在 `idShort` 上，
       而 `_attr_val` 认的是列名 → 表格视图与引擎视图取法不同，两条路都得试。
    """
    # 编号两种写法都要认：台账里既有 AC-01（连字符），也有 EQ007 / R005（字母+数字连写）。
    m = re.search(r"([A-Za-z]{2,4})-(\d{1,4})", q)
    _named = None
    if m:
        dev = "%s-%s" % (m.group(1), m.group(2))
    else:
        m = re.search(r"(?<![A-Za-z])([A-Za-z]{1,4}\d{1,4})(?![0-9A-Za-z])", q)
        if m:
            dev = m.group(1)
        else:
            # 没编号，也可能是点了名（"供应商 Siemens 的所在地"）
            _named = _instance_by_name_value(q, data, D)
            if not _named:
                return None
            dev = str(_named).rsplit("_", 1)[-1]
    # 只处理「取值 / 归属」这类单点问句，不抢极值与计数。
    # "在哪个车间""属于哪个产线"也要接：那是属性直查，不是关系边。
    # "…的所在地"这种名词短语结尾同样是直查（没有动词，问的就是那条记录的某个字段）。
    if not re.search(r"(是多少|多大|值为|数值|在哪个|属于哪个|是哪个|是什么|叫什么)", q):
        # "…的所在地"这类名词短语结尾也算直查，但必须**不含疑问量词**——
        # "价格大于10.06的产品有多少"同样以"的产品有多少"结尾，把它当直查就答成单条记录了。
        if not re.search(r"的[\u4e00-\u9fff]{2,8}[?？]?$", q) or re.search(r"(多少|几|哪些|什么)", q):
            return None
    token = None
    # 属性词先走 _find_attr：它认词典、认本体属性 label、认字段名反译，
    # 比只查 attr_cn2en 宽（food 的"车间"就不在 attr_cn2en 里，会在下面漏掉）。
    _da_en, _da_cn = _find_attr(D, q)
    if _da_en and _da_cn:
        token, attr_en = _da_cn, _da_en
    for cn in sorted((D.get("attr_cn2en") or {}), key=len, reverse=True):
        if token:
            break
        if cn and len(cn) >= 2 and cn in q:
            token = cn
            break
    if not token:
        return None
    if not _da_en:
        attr_en = (D.get("attr_cn2en") or {})[token]
    pat = re.compile(r"(^|_)%s(?=_|$)" % re.escape(dev.replace("-", "_")), re.I)
    # 点名定位时直接用那一条；编号定位才按 pattern 全库找（同一编号可能挂在多张表上）。
    cands = [_named] if _named else [k for k in data if pat.search(str(k))]
    if not cands:
        return None
    up = attr_en.split("_")[0]
    # 🔴 定位必须**严格核对属性名**，不能"取第一个有值的节点"。
    #    否则 `AC_01` 下挂着 9 个属性节点，遍历顺序决定答哪个——
    #    实测答成「投用年份=56.0」（那是运行功率的值）、
    #    「厂商名=SN-AC01」（那是序列号）。属性名对不上就当没这条。
    def _node_val(rec):
        if not isinstance(rec, dict):
            return None
        # 引擎视图：一属性一实例，列名在 idShort、值在 value
        if rec.get("idShort") == attr_en or rec.get("idShort") == up:
            return rec.get("value")
        if attr_en in str(rec.get("aasPath", "")) or up in str(rec.get("aasPath", "")):
            return rec.get("value")
        # 表格视图：属性名就是列名
        return _attr_val(rec, attr_en, aliases, data)

    # 输出用台账里的名字，不用内部编号：问"供应商 Siemens"答"S2的所在地"
    # 等于把用户指认过的东西又换了个不认识的说法。
    _label = dev
    if cands:
        _label = _display_name(data.get(cands[0]) or {}, aliases, default=dev) or dev
    for k in cands:
        val = _node_val(data[k])
        if val not in (None, ""):
            return "%s的%s：%s" % (_label, token, val)
    # 点名了设备但该设备没有这一列 → 如实说没有，**不回退到全库统计**
    return "%s没有属性「%s」（该设备台账里没有这一列的取值）" % (_label, token)


def _class_in_q(cn, q):
    """问句是否提到了这个类的中文名。

    类 label 常比口语多一个字：本体写"原材料"，人问"原料"（省的是中间那个"材"）。
    所以在类名里删掉任意一个字后若能在问句里找到，就算提到。
    只对 3 字以上的类名放宽；"批次原料"删任一字都不成词，不会被"批次"误命中。
    """
    if not cn:
        return False
    if cn in q:
        return True
    if len(cn) >= 3:
        for i in range(len(cn)):
            if cn[:i] + cn[i + 1:] in q:
                return True
    return False


def _pick_src_instance(q, data, D):
    """问句里点名的那个实例。

    三种排序都错过一次，最后定下这个规则：
      ① 按名字长短降序（越精确越好）—— 错。B001 既是 Food_batches_B001 也是
         Food_batch_ingredient_B001，取长的那个会挑到投料记录，然后按"批次→产品"
         去找边，一条都对不上。
      ② 只取第一个匹配 —— 错。取决于 dict 顺序，换个进程结果就变。
      ③ 现在：**先看问句提到的实体类**（类前缀对得上优先），再在同一档里取名字短的。
    短的那个就是用户嘴里的主记录，中间表的名字总是更长。
    """
    emap = _resolve_entity_map(D, data)
    pref_rank = [str(emap[c]).lower() for c in sorted(emap, key=len, reverse=True)
                 if c and _class_in_q(c, q)]
    cand = []
    for k in data:
        seg = str(k).rsplit("_", 1)[-1]
        if not (re.search(r"\d", seg) and seg in q):
            continue
        # 编号长度：原来的 >=3 会漏掉 E1 / L1 / S1 这种短编号（manufacturing 全是这种），
        # 而"设备 E1 在哪个产线"正是这一类。短编号要求"字母开头+数字结尾"，
        # 免得纯数字（"有多少台 2"）被当成实例。
        if len(seg) >= 3 or re.fullmatch(r"[A-Za-z]{1,4}\d{1,4}", seg):
            cand.append(k)
    if not cand:
        return None

    def rank(k):
        kl = str(k).lower()
        for i, p in enumerate(pref_rank):
            if p and (kl == p or kl.startswith(p + "_")):
                return (0, i, len(kl))
        return (1, 0, len(kl))

    return sorted(cand, key=rank)[0]


def _rel_list_lookup(q, data, D):
    """按本体 schema 走多条对象属性边，回答"X 用了哪些 Y"。

    与 _schema_rel_lookup 同一套依据（domain/range + 类中文名），区别是取多值：
    一个主语同一关系可能有多条边，压平后的 dict 只留最后一条，所以走 _rel_multi。
    """
    # "多少种/几种/多少个"也要接：问的是同一条关系边上有几个目标，
    # 「B001 批次用了多少种原料」= 这条边有几条（实测答案 2）。不收这种问法，
    # 它会落到"多少种"的类型计数分支，被当成类型值去数 → 拒答。
    if not re.search(r"哪些|列出|都有什么|有什么|用了|用了哪些|多少种|几种|多少个|多少条", q):
        return None
    cls_labels = D.get("_class_labels") or {}
    rel_schema = D.get("_rel_schema") or {}
    multi = D.get("_rel_multi") or {}
    if not cls_labels or not rel_schema or not multi:
        return None
    # 源实例：末段编号出现在问句里的实例。挑选规则见 _pick_src_instance——
    # B001 同时是批次和投料记录的编号，挑错就整条关系链断掉。
    _src = _pick_src_instance(q, data, D)
    if not _src:
        return None
    src = _src
    src_cls = str(src).rsplit("_", 1)[0]
    _emap = _resolve_entity_map(D, data)
    # 目标类：本体的类 label 常常就是类名本身（food 的 _class_labels 键值相同），
    # 拿它跟问句对不上。中文名只信 _resolve_entity_map —— 它验过本库的类。
    _cls_set = {str(c).lower() for c in _class_prefixes(data)}
    tgt = None
    tgt_cn = None
    for cn in sorted(_emap, key=len, reverse=True):
        _c = str(_emap[cn]).lower()
        if cn and _class_in_q(cn, q) and _c != src_cls.lower() and _c in _cls_set:
            tgt, tgt_cn = _c, cn
            break
    if not tgt:
        return None
    prop = None
    for pname, meta in rel_schema.items():
        if (str(meta.get("domain") or "").lower() == src_cls.lower()
                and str(meta.get("range") or "").lower() == tgt.lower()):
            prop = pname
            break
    tgts = list(multi.get((src, prop)) or []) if prop else []
    if not tgts:
        # 两跳：源 --[p1]--> 中间类 --[p2]--> 目标。
        # food 的「批次用了哪些原料」就是这样：批次与原料之间没有直连边，
        # 中间隔着一层投料记录（Food_batch_ingredient，domain 指向它、
        # 它自己再用 belongsToBatch 指回批次）。只认一跳永远答不出这题。
        for p1, m1 in rel_schema.items():
            if str(m1.get("range") or "").lower() != src_cls.lower():
                continue
            mid = str(m1.get("domain") or "").lower()
            for p2, m2 in rel_schema.items():
                if str(m2.get("domain") or "").lower() != mid:
                    continue
                if str(m2.get("range") or "").lower() != tgt.lower():
                    continue
                for (s2, pn), vs in multi.items():
                    if pn != p1 or src not in vs:
                        continue
                    for t in (multi.get((s2, p2)) or []):
                        if t not in tgts:
                            tgts.append(t)
                if tgts:
                    break
            if tgts:
                break
    if not tgts:
        return None
    aliases = D.get("field_aliases") or {}
    names = []
    for t in tgts:
        d = data.get(t) or {}
        names.append(_display_name(d, aliases, default=str(t).rsplit("_", 1)[-1]))
    rid = str(src).rsplit("_", 1)[-1]
    _note_rows([(t, data.get(t) or {}) for t in tgts], tgt_cn, lambda d: rid)
    if re.search(r"多少种|几种|多少个|多少条", q):
        return "%s 的%s 共 %d 种（%s）" % (rid, tgt_cn, len(names), "、".join(names))
    return "%s 的%s（%d 条）：%s" % (rid, tgt_cn, len(names), "、".join(names))


def _schema_rel_lookup(q, data, D):
    """按本体 schema 走一条对象属性，回答"X 的 Y 是哪个 Z"。

    依据的全部是本体里已经建好的三样东西：对象属性的 domain / range、类的中文 label、
    实例之间的边。不依赖问句里写不写字段名。

    现场：`Valve_qc_Q001 checkedBy Valve_batches_B007` 这条边在 nt 里，
    `checkedBy` 的 domain=Valve_qc、range=Valve_batches、label="质检批次"。
    问"Q001 检测的是哪个批次"时，源实例属于 Valve_qc，问的"批次"是 Valve_batches，
    两条一夹，就是 checkedBy。

    只在单值问句上触发（哪个/哪一/是哪/是什么/属于），计数和列表问句不进来。
    """
    if not re.search(r"哪个|哪一个|是哪|是什么|属于|对应哪", q):
        return None
    cls_labels = D.get("_class_labels") or {}
    rel_schema = D.get("_rel_schema") or {}
    if not rel_schema:
        return None
    # 1) 源实例：问句里出现的实例 ID（Q001 / B007 / R005）。选法见 _pick_src_instance。
    _src = _pick_src_instance(q, data, D)
    if not _src:
        return None
    src = _src
    src_cls = str(src).rsplit("_", 1)[0]
    # 2) 目标类：问句里出现的中文类名，取最长、且不等于源类。
    # 中文名不信 _class_labels —— food 的 label 就是类名本身（Food_products），
    # 拿它跟问句里的"产品"对不上，整条单跳关系查询就废了。
    # 走 _resolve_entity_map：它把中文名映到本库真实存在的类，已验过一遍。
    _cls_set = {str(c).lower() for c in _class_prefixes(data)}
    _emap = _resolve_entity_map(D, data)
    tgt = None
    tgt_cn = None
    for cn in sorted(_emap, key=len, reverse=True):
        _c = str(_emap[cn]).lower()
        if cn and _class_in_q(cn, q) and _c != src_cls.lower() and _c in _cls_set:
            tgt, tgt_cn = _c, cn
            break
    if not tgt:
        return None
    # 3) 找 domain→range 对得上的对象属性；没有就把方向反过来找
    prop, reverse = None, False
    for pname, meta in rel_schema.items():
        if (str(meta.get("domain") or "").lower() == src_cls.lower()
                and str(meta.get("range") or "").lower() == tgt.lower()):
            prop = pname
            break
    if not prop:
        for pname, meta in rel_schema.items():
            if (str(meta.get("domain") or "").lower() == tgt.lower()
                    and str(meta.get("range") or "").lower() == src_cls.lower()):
                prop, reverse = pname, True
                break
    if not prop:
        return None
    aliases = D.get("field_aliases") or {}
    rid = str(src).rsplit("_", 1)[-1]

    def _disp(k):
        d = data.get(k) or {}
        return _display_name(d, aliases, default=str(k).rsplit("_", 1)[-1])

    plabel = rel_schema.get(prop, {}).get("label") or prop
    # 本体里属性 label 可能是"关联line"这种中英混写（建库时自动拼的）。
    # 问句里用户说的是中文类名，回答就用类名 —— "E1 的产线是 产线A" 比
    # "E1 的关联line是 产线A" 像人话。
    if tgt_cn and re.search(r"[A-Za-z]", str(plabel)):
        plabel = tgt_cn
    if not reverse:
        v = _attr_val(data.get(src) or {}, prop, aliases, data)
        if not v:
            return None
        hit = [k for k in data if str(k).endswith("_" + str(v))]
        _note_rows([(rid, data.get(src) or {})], plabel, lambda d: v)
        return "%s 的%s是 %s" % (rid, plabel, _disp(hit[0]) if hit else v)
    # 反向：目标类的实例里，谁的这条边指向源实例
    got = []
    for k, d in data.items():
        if not str(k).lower().startswith(tgt.lower()):
            continue
        if str(_attr_val(d, prop, aliases, data) or "") == rid:
            got.append(k)
    if not got:
        return None
    _note_rows([(k, data[k]) for k in got], plabel, lambda d: rid)
    return "%s 对应的%s是 %s" % (rid, tgt_cn, "、".join(_disp(k) for k in got[:20]))


def _relation_lookup(q, data, D):
    """M15-9：关系类问句（上下游 / 同一厂商）。答不出返回 None。

    症状：「AC-01 下游的设备是什么」只回显"（混合检索）找到相关实体: ..."，
    三个关系问句全废，而极值问句正常。

    根因：**引擎从来没读过 `first` / `second`**。AAS 映射把关系投成了
    独立节点（`AC_10_AssetData_connectedTo`），里面是
    `{'first': '...#urn_ledger_AC_10', 'second': '...#urn_ledger_DR_01'}`，
    但 `ontology_qa_v3` 全文没有一处引用这两个键——数据在图里，引擎不认识。
    而极值分支走的是属性值比较，跟关系无关，所以极值问句一直是好的。
    这也解释了为什么这个缺口一直没被发现：错的只有关系那一类。

    两种问法分别处理：
    - 「X 下游/上游的设备是什么」：沿 first→second 走，可多跳（数「下游」出现次数）
    - 「和 X 同一厂商的设备」：走属性值相同（不是关系边，是 shared_value）
    """
    m = re.search(r"([A-Za-z]{2,4})-(\d{1,4})", q)
    if not m:
        return None
    dev = "%s-%s" % (m.group(1), m.group(2))
    dev_und = dev.replace("-", "_")

    def _short(uri):
        """`https://example.org/aas#urn_ledger_AC_10` -> `AC-10`"""
        s = str(uri or "").rsplit("#", 1)[-1]
        s = s.replace("urn_ledger_", "")
        m2 = re.fullmatch(r"([A-Za-z]{2,4})_(\d{1,4})", s)
        return "%s-%s" % (m2.group(1), m2.group(2)) if m2 else s

    # ---- 上下游（可多跳）
    if "下游" in q or "上游" in q:
        hops = max(q.count("下游"), q.count("上游"))
        if hops < 1:
            return None
        down = "下游" in q
        # 建邻接表：关系节点名里带 first 端设备号，方向由 first/second 决定
        adj = defaultdict(set)
        for k, rec in data.items():
            if not isinstance(rec, dict) or "first" not in rec and "second" not in rec:
                continue
            a, b = _short(rec.get("first")), _short(rec.get("second"))
            if not a or not b or a == b:
                continue
            # AAS 的 first=上游设备、second=下游设备（产线链方向）
            if down:
                adj[a].add(b)
            else:
                adj[b].add(a)
        if not adj:
            return None
        frontier = {dev}
        seen = set()
        for _ in range(hops):
            nxt = set()
            for d in frontier:
                nxt |= adj.get(d, set())
            frontier = nxt - seen
            seen |= frontier
            if not frontier:
                break
        if not frontier:
            return "%s%s第 %d 跳没有设备（产线链到头了）" % (dev, "下游" if down else "上游", hops)
        # 🔴 文案必须带跳数。「下游的下游」答成"下游的设备：AC-03"会让人以为
        #    直接下游就是 AC-03，答案对但表述误导（实测踩到）。
        _hop = "" if hops == 1 else "第 %d 跳" % hops
        return "%s%s%s的设备：%s" % (
            dev, "下游" if down else "上游", _hop, "、".join(sorted(frontier)))

    # ---- 同一厂商（走属性值相同，不是关系边）
    if "同一厂商" in q or "同一厂家" in q or "同厂商" in q:
        mfr_en = None
        for en in ("ManufacturerName", "SupplierName", "VendorId", "厂商"):
            if any(en in str(k) for k in data):
                mfr_en = en
                break
        if not mfr_en:
            return None
        mine = None
        pat = re.compile(r"(^|_)%s(?=_|$)" % re.escape(dev_und))
        for k, rec in data.items():
            if mfr_en in str(k) and pat.search(str(k)):
                mine = rec.get("value")
                break
        if not mine:
            return "%s没有厂商信息（台账里它的厂商名是空的）" % dev
        peers = set()
        for k, rec in data.items():
            if mfr_en in str(k) and rec.get("value") == mine:
                # 🔴 别用 `_([A-Za-z]{2,4}_\d{1,4})_` 这种模式：键是
                #    `urn_ledger_AC_01_AssetData_ManufacturerName`，
                #    资产号**后面跟的是 `_AssetData`**，模式要求设备号后面紧跟 `_`，
                #    结果一个都匹配不上 → 答"只有它自己一台"（实测 10 台说成 1 台）。
                #    直接复用 _short：先剥 urn_ledger_ 前缀拿 IRI 尾段，不够再从
                #    aasPath 首段取（属性节点上就有 `AC-01/AssetData/ManufacturerName`）。
                peer = None
                ap = str(rec.get("aasPath") or "")
                if ap:
                    m3 = re.match(r"([A-Za-z]{2,4}-\d{1,4})", ap)
                    if m3:
                        peer = m3.group(1)
                if not peer:
                    segs = [s for s in str(k).split("_")
                            if re.fullmatch(r"[A-Za-z]{2,4}|\d{1,4}", s)]
                    for i in range(len(segs) - 1):
                        if (re.fullmatch(r"[A-Za-z]{2,4}", segs[i])
                                and re.fullmatch(r"\d{1,4}", segs[i + 1])):
                            peer = "%s-%s" % (segs[i], segs[i + 1])
                            break
                if peer and peer != dev:
                    peers.add(peer)
        if not peers:
            return "%s 的厂商（%s）只有它自己一台" % (dev, mine)
        return "和 %s 同一厂商（%s）的设备共 %d 台：%s" % (
            dev, mine, len(peers) + 1, "、".join(sorted(peers)))

    return None


def answer(q, data, D):
    """词典 D 驱动的通用问答。"""
    aliases = D.get("field_aliases", {})
    cn2cn = D.get("attr_en2cn", {})

    # M15-9 关系类：与直查同层，理由同上——必须在任何兜底之前。
    # 先按本体 schema 走（domain/range 对得上就用那条边），再走旧的编号式关系问句。
    _srel = _schema_rel_lookup(q, data, D)
    if _srel:
        return _srel
    _lrel = _rel_list_lookup(q, data, D)
    if _lrel:
        return _lrel
    _rel = _relation_lookup(q, data, D)
    if _rel:
        return _rel

    # M15-8 直查：必须放在函数最前面。它原来被放在「类型枚举」分支内部
    # （宿主 `if "类型" in q and ...`），所以直查问句根本走不到——改了四版都不生效，
    # 根因是位置不是逻辑。**改引擎先确认缩进层级。**
    _direct = _device_attr_lookup(q, data, D, aliases)
    if _direct:
        return _direct

    # 计数量词：按问题中的实体词选合适量词（设备→台 / 测线→条 / 船→艘 / 项目→个 / 客户→家），缺省"个"。
    # 提到函数头: 组合模板(状态+类型)/状态计数/类型计数/实体总数 多处分摊使用。
    _MEASURE = {"设备": "台", "测线": "条", "线": "条", "船": "艘", "产品": "个", "书": "本",
                "图书": "本", "项目": "个", "订单": "个", "批次": "批", "客户": "家", "炮点": "个", "质检": "个"}

    def _measure_for(question):
        for _c, _m in _MEASURE.items():
            if _c in question:
                return _m
        return "个"

    # 显示名辅助
    def names(matched):
        return [_display_name(d, aliases, default=n) for n, d in matched]

    # ---- 布尔故障字段识别(前置): 具名属性值全为 0/1 时, 识别为布尔字段 ----
    # 坑(P0-评测口径): "机器故障标签" 这类 0/1 布尔字段, 属性名含"故障"(命中状态词)
    # 又含"机器"(命中类型词), 会被下方 [状态]的[类型] 组合模板劫持成"故障的机器共 N",
    # 导致过滤计数/求和/平均/极值全部答错。此处前置识别布尔字段的数值意图,
    # 在组合模板之前返回确定性答案, 统一评测口径(实测 58/61 → 61/61)。
    _bf_attr_en, _bf_attr_cn = _find_attr(D, q)
    _bf_vals = ({str(_field(d, _bf_attr_en, aliases)).strip() for d in data.values()}
                if _bf_attr_en else set())
    _bf_vals = {v for v in _bf_vals if v != ""}
    _is_bool_field = bool(_bf_attr_en) and _bf_vals <= {"0", "1"} and "1" in _bf_vals
    _bf_eq = re.search(r'[=＝]\s*(-?\d+(?:\.\d+)?)\s*的?\s*(数量|多少|共)', q)
    _bf_num_intent = bool(
        _bf_eq
        or any(k in q for k in ("总", "合计", "总和", "平均", "均值"))
        or _EXTREME.search(q)
    )
    if _is_bool_field and _bf_num_intent:
        _bf_cname = _bf_attr_cn or cn2cn.get(_bf_attr_en, _bf_attr_en)
        if _bf_eq:  # 布尔字段过滤计数: "机器故障标签=1 的数量"
            _tgt = _bf_eq.group(1)
            _tv = float(_tgt)
            _n = sum(1 for d in data.values()
                     if _num(_field(d, _bf_attr_en, aliases)) is not None
                     and float(_num(_field(d, _bf_attr_en, aliases))) == _tv)
            if _n == 0:  # 字符串存储兜底
                _n = sum(1 for d in data.values()
                         if str(_field(d, _bf_attr_en, aliases)).strip() == _tgt)
            return "%s=%s 的数量是 %d" % (_bf_cname, _tgt, _n)
        if any(k in q for k in ("总", "合计", "总和")):  # 布尔字段求和(即1的计数)
            _vals = [_num(_field(d, _bf_attr_en, aliases)) for d in data.values()]
            _vals = [v for v in _vals if v is not None]
            if _vals:
                return "%s总和 %.2f" % (_bf_cname, sum(_vals))
        if "平均" in q or "均值" in q:  # 布尔字段平均
            _vals = [_num(_field(d, _bf_attr_en, aliases)) for d in data.values()]
            _vals = [v for v in _vals if v is not None]
            if _vals:
                return "%s平均值 %.2f (%d条)" % (_bf_cname, sum(_vals) / len(_vals), len(_vals))
        if _EXTREME.search(q):  # 布尔字段极值(最大/最小)
            _sub = _entity_subset(q, D, data)
            _items = [(d, _num(_field(d, _bf_attr_en, aliases))) for d in _sub.values()]
            _items = [(d, v) for d, v in _items if v is not None]
            if _items:
                _is_max_v = _is_max(q)
                _best = max(_items, key=lambda x: x[1]) if _is_max_v else min(_items, key=lambda x: x[1])
                return "%s的记录: %s (%s=%s)" % (
                    ("最大" if _is_max_v else "最小") + _bf_cname,
                    _display_name(_best[0], aliases, default=""), _bf_cname, _best[1])

    # ---- 组合: [状态]的[类型] (报警的焊接机器人) ----
    st_en, st_cn = _find_enum(D, q, "status", data)
    ty_en, ty_cn = _find_enum(D, q, "type")
    if st_en and ty_en:
        matched = _note_rows(
            [(n, d) for n, d in data.items()
             if _status_matches(d, st_en, aliases, D) and _type_matches(d, ty_en, aliases)],
            "%s+%s" % (st_cn, ty_cn), lambda d: "%s/%s" % (st_en, ty_en))
        nm = names(matched)
        if "多少" in q:
            return "有 %d %s%s的%s" % (len(nm), _measure_for(q), st_cn, ty_cn)
        if "列出" in q:
            return "列出所有%s的%s:\n%s" % (st_cn, ty_cn, _fmt_names(nm)) if nm else "无%s的%s" % (st_cn, ty_cn)
        return "%s的%s共 %d" % (st_cn, ty_cn, len(nm))

    # ---- 区域关系 ----
    zo_en, zo_cn = _find_enum(D, q, "zone")
    if zo_en and any(k in q for k in ("区域", "区的", "在", "哪些", "有哪些")):
        def _zmatch(d):
            for f in ("location", "zone", "region", "area", "workshop"):
                if _field(d, f, aliases) == zo_en:
                    return True
            return False
        matched = _note_rows([(n, d) for n, d in data.items() if _zmatch(d)],
                             zo_cn, lambda d: zo_en)
        nm = names(matched)
        if "多少" in q:
            return "%s区域有 %d" % (zo_cn, len(nm))
        return "%s区域的记录(%d):\n%s" % (zo_cn, len(nm), _fmt_names(nm))

    # ---- 聚合/分组查询 (规则引擎, 确定性): '各X的Y' 与 'XX分布' ----
    # 词典驱动: attr_cn2en 识别 车间/类型/状态 等分组属性; 实体消歧限定聚合范围。
    # 放在通用"类型枚举/列出"之前, 避免"各类型产品数"被误判成"类型有: ..."列表。
    _agg_en, _agg_cn, _agg_mode = _find_agg_attr(D, q)
    if _agg_en and (_agg_mode == "group" or _agg_mode == "dist"):
        _dist, _nonempty = _agg_distribution(q, D, data, _agg_en)
        _lines = "\n".join(f"  {k}: {v}" for k, v in _dist.items())
        if _agg_mode == "dist":
            return "%s分布:\n%s" % (_agg_cn, _lines)
        return "各%s:\n%s" % (_agg_cn, _lines)

    attr_en, attr_cn = _find_attr(D, q)

    # ---- 过滤计数: 属性=N 的数量 (机器故障标签=1 的数量) ----
    # 必须排在状态/类型词模板之前: 属性中文名可能含状态词(如"故障"),
    # 若被状态模板先命中会返回"有 N 故障的", 过滤计数永远不达。
    m_eq = re.search(r'[=＝]\s*(-?\d+(?:\.\d+)?)\s*的?\s*(数量|多少|共)', q)
    if attr_en and m_eq:
        target = m_eq.group(1)
        tv = float(target)
        n = sum(1 for d in data.values()
                if _num(_field(d, attr_en, aliases)) is not None
                and float(_num(_field(d, attr_en, aliases))) == tv)
        if n == 0:  # 可能以字符串存储
            n = sum(1 for d in data.values() if str(_field(d, attr_en, aliases)).strip() == target)
        cname = attr_cn or cn2cn.get(attr_en, attr_en)
        return "%s=%s 的数量是 %d" % (cname, target, n)

    # ---- 实体总数: 有多少台设备/产品总数 等 (实体类, 非类型值) ----
    # 实体类中文名 -> URI 子串。仅当问题里"多少[台个条]/总数/共多少"直接修饰实体类词时才触发，
    # 从而不误伤"有多少台空压机"(空压机是 deviceType 值, 走下方类型模板)。
    # 优先用建库时 data profiling 生成的 entity_cn2en {中文实体名: 表名}（测线/炮点/项目/船…），
    # 兜底保留原硬编码映射（兼容旧词典）。
    # 数据来源 lexicon.get_entity_cn2uri()（与原 _ENTITY_CN2URI 键值逐一一致）。
    # 实体类中文名 -> 本库真实类名前缀。词典里的表名先在本库核对一遍再使用：
    # 词典串库（chem 的 entity_cn2en 写成 mach_products）会让计数恒为 0。
    _entity_map = _resolve_entity_map(D, data)
    # 计数量词 _MEASURE / _measure_for 已在 answer 函数头定义, 此处不再重复。
    # 跨行业泛化守卫：若问题含 状态/类型/区域 枚举词，则这是"过滤计数"而非"实体总数"，
    # 不得在此返回实体总数（否则"有多少台设备在运行"会被误答成"有 6 台设备"），
    # 须落空继续走到下方的状态/类型计数分支。
    # 问句里直接点了类型值（"H类机器有多少台"）时，这句是**类型过滤计数**，
    # 不是实体总数。不先认出来，会被下面的"机器有多少"当成"全部机器"答 10000。
    _dv_ty, _dv_cn = _enum_value_in_data(q, data, aliases)
    for cn in sorted(_entity_map, key=len, reverse=True):
        if (re.search(r'多少[台个条艘本家位名间张份套批辆只头棵户门]?' + cn, q)  # 有多少台设备 / 有多少家供应商
                or re.search(cn + r'(总数|共有多少|有多少|共多少)', q)  # 设备总数 / 炮点总数
                or re.search(r'共\s*多少\s*' + cn, q)):   # 共多少设备
            # 跨行业泛化守卫：被状态/类型/区域词修饰时跳过实体总数（走下方过滤计数）。
            # 关键: 直接用完整问句判定，但排除"命中的枚举词恰好等于实体词本身"的情况。
            # 若先把实体词 replace 掉，类型词含实体词时（"试压设备"含"设备"）会被破坏成"试压"，
            # _find_enum 匹配不到 → 守卫失效 → 误返回实体总数。
            # 反之若不做排除，"设备"既作实体类名又被 _find_enum 当成类型值 → 守卫恒真
            # → "有多少台设备"被误跳过实体总数, 落到类型计数返回"有 0 设备"。
            # 数值比较/区间问法(大于/小于/超过/以上/以下/到/之间)不是"实体总数"问句,
            # 必须落到下方 range 分支按属性阈值过滤计数。否则问句里的"X有多少"会被
            # 误当实体总数 —— 如"机器中工艺温度大于310.01的机器有多少"答成"有 10000 个机器"
            # (批 2 已知缺陷, 评测误答)。
            if (any(k in q for k in ("大于", "小于", "高于", "低于", "超过", "少于",
                                     "以上", "以下"))
                    or "之间" in q or re.search(r"\d\s*到\s*\d", q)):
                continue
            # 跨行业泛化守卫（M15-2 补）：问句里出现「属性名 + 为/是/= + 值」时，
            # 这是**等值过滤计数**，不是实体总数。
            # 实测（AAS 台账，NorthAir 真值 5 台）：
            #   「厂商名为 NorthAir 的设备有多少台」→ 本分支抢先 → 答 50（错）
            #   「厂商名为 NorthAir 的设备有几台」  → 落到下方等值过滤 → 答 5（对）
            # 同一语义只因量词不同就差 10 倍，说明不是算错，是**路由错**。
            # 本分支的触发式 `多少[台个条艘本]?设备` 命中「多少台设备」，而守卫只看
            # 状态/类型/区域枚举词，看不到「属性=值」，于是放行。
            _eq_guard = re.search(r'(?:为|是|＝|=)\s*([^\s的，,。?？、]+)', q)
            _eq_stop = ("多少", "什么", "几", "哪些", "哪种", "几何", "如何", "怎样", "谁")
            _eq_attr_guard, _ = _find_attr(D, q)
            if (_eq_attr_guard and _eq_guard
                    and _eq_guard.group(1).strip() not in _eq_stop):
                continue
            if _dv_ty:
                continue
            _t_en, _t_cn = _find_enum(D, q, "type")
            _s_en, _s_cn = _find_enum(D, q, "status", data)
            _z_en, _z_cn = _find_enum(D, q, "zone")
            if ((_t_en and _t_cn != cn)
                    or (_s_en and _s_cn != cn)
                    or (_z_en and _z_cn != cn)):
                continue
            uri_sub = _entity_map[cn]
            _hit = _note_rows(_inst_rows(data, uri_sub),
                              "计数", lambda d: 1)
            n = len(_hit)
            if n == 0:
                # 这一类在本库里一个实例都没有 —— 说明词典里的这条只是通用词表带来的，
                # 这个库根本没有这种东西。答"有 0 个产品"是把"我不知道"说成了"没有"，
                # 落到下面的分支去拒答更诚实（ai4i 台账被答"有 0 个产品"就是这样来的）。
                continue
            if "总数" in q:
                return "%s总数 %d" % (cn, n)
            return "有 %d %s%s" % (n, _MEASURE.get(cn, "个"), cn)

    # ---- 数量: 状态/类型/区域 ----
    # 跨行业泛化增强：\"多少种/多少类\" = 某实体类下类型值去重计数
    # （\"一共有多少种产品\" → 产品类型的去重个数，而非产品记录总数）
    if re.search(r'(多少|几)(种|类)', q):
        _sub = _entity_subset(q, D, data)
        # 问句点名了某个属性（"产品的压力等级有多少种"）时，统计那个属性的去重取值。
        # 旧版无脑数 type 列，于是压力等级的 4 种被答成产品类型的 6 种 —— 答的是另一个字段。
        _qa_en, _qa_cn = _find_attr(D, q)
        if _qa_en and _qa_en.lower() not in ("type", "category", "kind", "devicetype"):
            _qa_vals = {str(_attr_val(d, _qa_en, aliases, _sub) or "").strip() for d in _sub.values()}
            _qa_vals = {v for v in _qa_vals if v}
            if _qa_vals:
                _note_rows([(k, d) for k, d in _sub.items()
                            if str(_attr_val(d, _qa_en, aliases, _sub) or "").strip()],
                           _qa_cn, lambda d: _attr_val(d, _qa_en, aliases, _sub))
                return "共有 %d 种%s" % (len(_qa_vals), _qa_cn)
        # 问句问的是"X 的 Y 有多少种"，但 Y 在本库解析不出属性（food 的"车间"、
        # manufacturing 的"区域"）——此时不能拿类型列去顶上，那是在答另一个字段。
        # 宁可拒答。问句本身问的就是类型（含"类型/类别/品类/品种/种类"）时不拦。
        _named_attr = re.search(r"的([\u4e00-\u9fff]{2,6}?)(?:有|是)?(?:多少|几)", q)
        if _named_attr and not _qa_en:
            _tok = _named_attr.group(1)
            if not any(w in _tok for w in ("类型", "类别", "品类", "品种", "种类")):
                return MISS_ANSWER
        # 别名表常常只给出"另一列也叫这个"（food 的 deviceType→category），
        # 漏掉字面列名 deviceType 本身，于是"设备的设备类型有多少种"数不出值。
        _ty_cols = list(dict.fromkeys(
            [a for a in (aliases.get("deviceType") or [])]
            + ["deviceType", "category", "type", "kind"]))
        _ty_vals = set()
        for _d in _sub.values():
            for _c in _ty_cols:
                _v = str(_d.get(_c) or "").strip()
                if _v:
                    _ty_vals.add(_v)
                    break
        if _ty_vals:
            _note_rows([(k, d) for k, d in _sub.items()
                        if any(str(d.get(_c) or "").strip() for _c in _ty_cols)],
                       "类型", lambda d: next((str(d.get(_c) or "").strip()
                                             for _c in _ty_cols if str(d.get(_c) or "").strip()), ""))
            return "共有 %d 种%s" % (len(_ty_vals), "类型")
    st_en, st_cn = _find_enum(D, q, "status", data)
    # "合格"这类词可能同时是别的属性的值（原料的 qc_result），被 status 值表误收。
    # 于是"原料中质检结果为合格的有多少条"在这里被 status 分支抢走 → 在原料表上找
    # status=合格 → 答 0（正确答案 9）。问句里点名了别的属性时，让给下面的通用过滤分支。
    _pre_attr, _ = _find_attr(D, q)
    if st_en and ("多少" in q or "数量" in q) and (not _pre_attr or str(_pre_attr) == "status"):
        sub = _entity_subset(q, D, data)  # 实体消歧: 只在该实体类实例中过滤
        n = sum(1 for d in sub.values() if _status_matches(d, st_en, aliases, D))
        return "有 %d %s%s的" % (n, _measure_for(q), st_cn)
    # ── 2026-09-15 补：任意属性等值过滤计数 ──
    # "批次配料中原料编号为R007的有多少条" / "客户中信用等级为A的有多少条"
    # 旧版只实现了 status 枚举过滤（上一分支），普通属性（raw_id / credit_level / check_item）
    # 的"X 中 Y 为 Z"没有分支 → 问句被路由到关联/计数分支 → 答出无关数字（不是算错，是没算）。
    # 这类错答最危险：数字读起来像正常回答，不像"无X"那样明显崩坏。
    _af_attr, _af_cn = _find_attr(D, q)
    _af_val = re.search(r'(?:为|是|＝|=)\s*([^\s的，,。?？、]+)', q)
    # 疑问词不能被当成过滤值（"功率为多少" → 会捕获"多少" → 错答"有 0 条"）
    _af_stop = ("多少", "什么", "几", "哪些", "哪种", "几何", "如何", "怎样", "谁")
    if _af_attr and _af_val and _af_val.group(1).strip() not in _af_stop \
            and ("多少" in q or "数量" in q or "几条" in q or "几台" in q):
        _af_target = _af_val.group(1).strip()
        _af_sub = _entity_subset(q, D, data)

        def _af_get(dd):
            # 统一走 _attr_val（camelCase + 对象属性按图结构剥末段），取不到返回 ""
            return _attr_val(dd, _af_attr, aliases, data) or ""

        # 先做实体消歧（不掺主键回落），再谈取值 ——
        # 顺序很重要：主键回落会让**错误的表**也能取到值（products 的 id 回落成 P001…），
        # 消歧因此不再触发，"批次的编号"被答成产品编号。
        def _af_vals_of(_s):
            return [_af_get(_d) for _d in _s.values()]

        _af_present = _af_vals_of(_af_sub)
        if not any(_af_present):
            for _cn in sorted((D.get("entity_cn2en") or {}), key=len, reverse=True):
                if _cn not in q:
                    continue
                _uri = str((D.get("entity_cn2en") or {})[_cn]).lower()
                _cand = dict(_inst_rows(data, _uri))
                _vals = _af_vals_of(_cand)
                if any(_vals):
                    _af_sub, _af_present = _cand, _vals
                    break

        # 消歧定表后，主键取不到再回落到实例名末段
        # （Chem_batches_B003 的主键 B003 只在实例名里，字段中没有 id）。
        # 只对主键回落；对 product_id 这类外键回落会拿到本行自己的 ID，直接答错。
        if not any(_af_present) and (_af_attr in ("id",) or _af_attr.lower().endswith("_key")):
            for _i, (_n, _d) in enumerate(_af_sub.items()):
                if not _af_present[_i]:
                    _seg = str(_n).rsplit("_", 1)[-1]
                    if _seg and _seg != str(_n):
                        _af_present[_i] = _seg
        if _af_sub and any(_af_present):
            # 匹配口径（大小写不敏感，等值优先，其次前缀）：
            #  · 数据属性值是字面量（"机加车间" / "A" / "R007"）
            #  · 对象属性取回来的是实例 ID（Valve_raw_materials_R009），需再剥一层末段
            #    才能与目标值 R007 对齐 —— 这是关系表过滤绕不开的一步。
            def _af_variants(v):
                vs = {v.lower()}
                tailseg = v.rsplit("_", 1)[-1]
                if tailseg:
                    vs.add(tailseg.lower())
                return vs
            _af_t = _af_target.lower()
            _af_n = sum(1 for _v in _af_present if _v and _af_t in _af_variants(_v))
            if not _af_n:
                _af_n = sum(1 for _v in _af_present
                            if _v and any(x.startswith(_af_t) for x in _af_variants(_v)))
            _af_cn_name = _af_cn or cn2cn.get(_af_attr, _af_attr)
            return "有 %d 条（%s为%s）" % (_af_n, _af_cn_name, _af_target)
    ty_en, ty_cn = _find_enum(D, q, "type")
    if not ty_en:
        # 词典没收录这个类型值时，从数据的类型列里认（"H类机器" → H）。
        ty_en, ty_cn = _enum_value_in_data(q, data, aliases)
    #  ① 实体名会被 _find_enum 当类型取值（"设备的功率最大是多少" → ty_en="设备"）
    #     → n 恒为 0 → 错答"有 0 台设备"。
    #  ② 极值问句（最X）应归下方 TopN/极值分支，不能被类型计数抢答。
    _emap_t = dict(get_entity_cn2uri()); _emap_t.update(D.get("entity_cn2en", {}) or {})
    _ty_is_entity = any(str(v).lower() in data and str(v).lower() in str(ty_en).lower()
                        for v in _emap_t.values())
    if ty_en and ("多少" in q or "数量" in q) and not _ty_is_entity and not _EXTREME.search(q):
        sub = _entity_subset(q, D, data)
        _tm = _note_rows([(k, d) for k, d in sub.items() if _type_matches(d, ty_en, aliases)],
                         ty_cn, lambda d: ty_en)
        if not _tm:
            # 一条都没有。type_cn2en 里塞满了通用模板词（锅炉/空压机/离心泵…），
            # 本库没有这类设备时，答"有 0 个锅炉"是把"我不知道"说成"没有"
            # （manufacturing / valve / auto_parts 各中一题，判 empty_zero）。
            # 没有证据就拒答，与实体计数分支同一条纪律。
            return MISS_ANSWER
        return "有 %d %s%s" % (len(_tm), _measure_for(q), ty_cn)
    # 类型词 + 的：按类型过滤计数/列出（"大气治理的项目" / "油轮的" 等，非"多少"式）
    # 极值消歧(P1): "容量最大的发电机组"里"发电机"是 type_cn2en 子串会误命中此处,
    # 而问题实为"最X的Y"极值查询(attr_en 已解析 + 极值词)。极值应优先于类型列举,
    # 否则会错误返回"无发电机"。故类型列举仅在非极值查询时触发。
    if ty_en and "的" in q and not (_EXTREME.search(q) and attr_en) \
            and not any(k in q for k in ("哪些", "列出", "有哪", "都有什么", "有什么")):
        # 修 2026-09-15: 此分支语义是"按类型值过滤"（如"大气治理的项目"），
        # 但中文"的"还承担领属结构（"设备的设备类型有哪些" = 设备的[类型]），
        # 两者被同一条件吞掉 → 枚举问句被误判为过滤 → matched 空 → 错答"无设备"。
        # 故排除枚举型问句，交给下方 L768 的"列出/有哪些"分支处理。
        sub = _entity_subset(q, D, data)
        matched = _note_rows([(n, d) for n, d in sub.items() if _type_matches(d, ty_en, aliases)],
                             ty_cn, lambda d: ty_en)
        nm = names(matched)
        return "%s(%d):\n%s" % (ty_cn, len(nm), _fmt_names(nm)) if nm else "无%s" % ty_cn

    # ---- 列出 / 有哪些 / 信息 ----
    if "列出" in q or "哪些" in q or "有哪些" in q or "信息" in q or "详情" in q or ("类型" in q and "多少" not in q):
        # 问"XX类型/有哪些类型"且无具体值时 → 枚举该 type 的值(图书类型等)
        if "类型" in q and "哪些" not in q and "列出" not in q and "的" not in q:
            # bug-B: 按实体类隔离枚举类型值, 避免多实体 KB(产品 category + 设备 deviceType)混排。
            # 用 entity_cn2en 的类名(英文表名)子串给每条记录归类(复用 _agg_distribution 同类机制),
            # 逐实体类分别枚举其类型取值并标注中文类名; 问题点名实体类时(_entity_subset 已缩域)只列该类。
            _en2cn = {str(v).lower(): cn for cn, v in (D.get("entity_cn2en") or {}).items()
                      if isinstance(v, str) and v}
            _cls = sorted(_en2cn, key=len, reverse=True)
            if not _cls:  # 该KB无实体类词表时兜底 lexicon 内置实体映射
                _e2c = {str(v).lower(): cn for cn, v in (get_entity_cn2uri() or {}).items()}
                _cls = sorted(_e2c, key=len, reverse=True)
                _en2cn = _e2c
            _scoped = _entity_subset(q, D, data)
            _restrict = (_scoped is not data)   # 问题点名了某个实体类
            _cvals = defaultdict(set)
            for _k, _d in data.items():
                if _restrict and _k not in _scoped:
                    continue
                _c = next((c for c in _cls if c and c in _k.lower()), None)
                if not _c:
                    continue
                _v = _agg_field_value(_d, "type", aliases)
                if _v and _v != "未知":
                    _cvals[_c].add(_v)
            _seg = ["%s类型有：%s" % (_en2cn.get(c, c), "、".join(sorted(_cvals[c])))
                    for c in sorted(_cvals)]
            if _seg:
                _note_rows([(k, d) for k, d in data.items()
                            if (not _restrict or k in _scoped)
                            and _agg_field_value(d, "type", aliases) not in (None, "", "未知")],
                           "类型", lambda d: _agg_field_value(d, "type", aliases))
                return "；".join(_seg)
            # 退化兜底: 无法按实体类归类(无实体类词表)时沿用原全量去重枚举
            ty_vals = sorted({str(d.get("deviceType") or d.get("category") or "") for d in data.values()} - {""})
            if ty_vals:
                _note_rows([(k, d) for k, d in data.items()
                            if str(d.get("deviceType") or d.get("category") or "")],
                           "类型", lambda d: d.get("deviceType") or d.get("category"))
                return "类型有：%s" % "、".join(ty_vals)
        # ── 2026-09-15 补：带实体限定的属性取值枚举 ──
        # "设备的设备类型有哪些" / "产品的压力等级有哪些" 这类最主流的中文问法，
        # 旧版没有实现（L770 的枚举分支要求"哪些"/"的"都不出现 → 只能处理裸问句），
        # 且问句里的实体名会被 _find_enum 误当类型值 → 落到下面 L812 返回"无设备"。
        # 这里在 status/type 分支之前，按"问题里的属性词"枚举该属性的去重取值。
        # 属性词统一走 _find_attr：它认词典、认本体属性 label、认字段名反译，
        # 比只查 attr_cn2en 宽。food 的设备表在 attr_cn2en 里一个词都没有，
        # 旧代码在这里拿不到 _attr_token，直接落到下面罗列设备名。
        _attr_token, _attr_en_tok = None, None
        _fa_en, _fa_cn = _find_attr(D, q)
        if _fa_en and _fa_cn and len(_fa_cn) >= 2 and _fa_cn in q:
            _attr_token, _attr_en_tok = _fa_cn, _fa_en
        else:
            for _a_cn in sorted((D.get("attr_cn2en") or {}), key=len, reverse=True):
                if _a_cn and len(_a_cn) >= 2 and _a_cn in q:
                    _attr_token = _a_cn
                    _attr_en_tok = (D.get("attr_cn2en") or {})[_a_cn]
                    break
        # M15-2：属性被「为/是/= 值」约束时，问的是**符合条件的实例**，不是属性的取值集合。
        # 实测「厂商名为 NorthAir 的设备有哪些」被本分支当成"厂商名有哪些取值"→
        # 答成六个厂商名的罗列。无约束的「设备的厂商名有哪些」不受影响（q 里没有 为/是/=）。
        _eqv = re.search(r'(?:为|是|＝|=)\s*([^\s的，,。?？、]+)', q)
        _eqv_stop = ("多少", "什么", "几", "哪些", "哪种", "几何", "如何", "怎样", "谁")
        _attr_constrained = bool(_eqv and _eqv.group(1).strip() not in _eqv_stop)
        # ── M15-8 直查已移到函数入口（_device_attr_lookup）──
        # 🔴 这一段原先被放在「类型枚举」分支的 if 块内（宿主是
        #    `if "类型" in q and ...`），所以「AC-01 的投用年份是多少」根本走不到——
        #    改了四版都没生效就是因为位置错了，不是逻辑错。教训：**改引擎先确认缩进层级**。

        if _attr_token and ("哪些" in q or "有哪" in q or "列出" in q) and not _attr_constrained:
            _attr_en = _attr_en_tok or (D.get("attr_cn2en") or {})[_attr_token]
            _sub = _entity_subset(q, D, data)
            # 类型/类别类字段名各库不一（type / category / deviceType / productType），
            # 取不到时按同族字段兜一遍，避免"图书的类型有哪些"返回空。
            _TYPEISH = {"type", "category", "kind", "class", "devicetype",
                        "device_type", "producttype", "product_type"}
            def _av(_d):
                v = _attr_val(_d, _attr_en, aliases, data)
                if not v and str(_attr_en).lower() in _TYPEISH:
                    for _f in ("deviceType", "category", "type", "kind",
                               "productType", "product_type"):
                        v = _field(_d, _f, aliases)
                        if v:
                            break
                return v
            # 取值统一走 _attr_val：兜 camelCase（多词列名）与对象属性（关系表外键，
            # 值是指向目标实例的 URI，需剥末段取 ID —— 如 R001/P003）。
            def _collect(_s):
                vals = {v for v in (_av(_d) for _d in _s.values()) if v}
                if not vals and _attr_en in ("id",):
                    # 主键常被编码进实例名（Customers_C001 / Valve_batches_B009），
                    # 实例 dict 里就没有 id 字段 —— 此时从实例名取末段。
                    for _name in _s:
                        seg = str(_name).rsplit("_", 1)[-1]
                        if seg and seg != str(_name):
                            vals.add(seg)
                return sorted(vals)
            _vals = _collect(_sub)
            # 实体消歧：问句里点名的表和属性所属表可能不一致
            # （"批次的产品编号" → "产品"命中实体名，选到 products，而该属性在 batches 上）。
            # 取不到值时按问句里出现的其它实体名依次重试，只认问句真正提到的表。
            if not _vals:
                for _cn in sorted((D.get("entity_cn2en") or {}), key=len, reverse=True):
                    if _cn not in q:
                        continue
                    _uri = str((D.get("entity_cn2en") or {})[_cn]).lower()
                    _cand = dict(_inst_rows(data, _uri))
                    _cv = _collect(_cand)
                    if _cv:
                        _sub, _vals = _cand, _cv
                        break
            # 再兜一层：问句点名的表根本没有该属性（"批次的存储条件" → 批次表无 storage，
            # 而它长在产品表上）。此时按属性反查 —— 全库只有一张表持有该属性就用它，
            # 多于一张则不猜（避免把"编号"这类常见属性随便安到别的表上）。
            if not _vals:
                _owners = []
                for _cn2, _uri2 in (D.get("entity_cn2en") or {}).items():
                    _cand2 = dict(_inst_rows(data, _uri2))
                    if _collect(_cand2):
                        _owners.append(_cand2)
                if len(_owners) == 1:
                    _sub = _owners[0]
                    _vals = _collect(_sub)
            if _vals:
                _owner = next((c for c in sorted((D.get("entity_cn2en") or {}), key=len, reverse=True)
                               if c and c in q), "")
                _prefix = f"{_owner}的{_attr_token}" if _owner else _attr_token
                _note_rows([(k, d) for k, d in _sub.items() if _av(d)],
                           _attr_token, _av)
                return "%s有：%s" % (_prefix, "、".join(_vals))
        st_en, st_cn = _find_enum(D, q, "status", data)
        if st_en:
            sub = _entity_subset(q, D, data)  # 实体消歧
            matched = _note_rows([(n, d) for n, d in sub.items()
                                  if _status_matches(d, st_en, aliases, D)],
                                 st_cn, lambda d: st_en)
            return "列出所有%s:\n%s" % (st_cn, _fmt_names(names(matched))) if matched else "无%s" % st_cn
        ty_en, ty_cn = _find_enum(D, q, "type")
        if ty_en:
            # 实体名被 _find_enum 误当类型值（问句必含实体名）→ 跳过，交下方实体实例枚举
            _emap_all = dict(get_entity_cn2uri()); _emap_all.update(D.get("entity_cn2en", {}) or {})
            _is_entity = any(str(v).lower() in data and str(v).lower() in str(ty_en).lower()
                             for v in _emap_all.values())
            if not _is_entity:
                matched = _note_rows([(n, d) for n, d in data.items()
                                      if _type_matches(d, ty_en, aliases)],
                                     ty_cn, lambda d: ty_en)
                return "列出所有%s:\n%s" % (ty_cn, _fmt_names(names(matched))) if matched else "无%s" % ty_cn
        # 实体实例列表: "项目有哪些/订单有哪些/有哪些船" → 枚举 entity_cn2en 对应类的实例
        # 🔴 这里不能再卡 `not ty_en`：上面「类型枚举」分支发现 ty_en 其实是问句里的
        # 实体名（"产品的品类有哪些"→ type 值表把"产品"当成了类型值）时会跳过且不返回，
        # 若此处仍要求 ty_en 为空，两边都不接，最后落到"暂不支持"（chem 实测 11 道空答）。
        # 能走到这里说明真正的类型枚举已经答完了，剩下的"哪些"就是问实例。
        if "哪些" in q:
            emap = dict(get_entity_cn2uri()); emap.update(D.get("entity_cn2en", {}) or {})
            for cn in sorted(emap, key=len, reverse=True):
                if cn in q:
                    uri_sub = emap[cn].lower()
                    ents = sorted({k for k, _d in _inst_rows(data, uri_sub)})
                    # M15-2：问句里点名了某个属性值就按它过滤，别枚举全部。
                    _fv = _filter_value_in_q(q, ents, data, cn)
                    if _fv:
                        ents = [e for e in ents
                                if any(isinstance(v, str)
                                       and v.strip().lower() == _fv.lower()
                                       for v in (data.get(e) or {}).values())]
                    if ents:
                        _note_rows([(e, data.get(e) or {}) for e in ents], cn)
                        names_list = [(_display_name(data[e], aliases, default=e.split('#')[-1])) for e in ents]
                        names_list = [n for n in names_list if n]
                        if names_list:
                            return "%s有：\n%s" % (cn, "、".join(names_list[:20]))
                        return "%s共 %d 个" % (cn, len(ents))

    # ---- TopN (属性最高/最低的N个) ----
    if attr_en and re.search(r'\d+\s*[台个条]', q) and _EXTREME.search(q):
        n = int(_extract_nums(q)[0]) if _extract_nums(q) else 3
        sub = _entity_subset(q, D, data)  # 极值消歧: 只在该实体类实例中求极值
        items = [(d, _num(_field(d, attr_en, aliases))) for d in sub.values()]
        items = [(d, v) for d, v in items if v is not None]
        is_max = _is_max(q)
        items.sort(key=lambda x: x[1], reverse=is_max)
        cname = attr_cn or cn2cn.get(attr_en, attr_en)
        rows = ["  - %s (%s=%s)" % (_display_name(d, aliases, default=""), cname, v) for d, v in items[:n]]
        return "%s%s的%d个:\n%s" % ("最高" if is_max else "最低", cname, n, "\n".join(rows))

    # ---- 单极值 (属性最大/最小/最长/最贵等) ----
    if attr_en and _EXTREME.search(q):
        # 语义陷阱拦截: "最X的Y" 里 Y 是抽象概念(安全/风险/措施/问题等)而非数值实体时,
        # 极值推断会把"最大的安全问题"误当成"容量最大"(只因为"最大"+数值字段匹配)。
        # 若问题含抽象概念词且无具体实体/属性指称, 不触发极值, 返回"暂不支持"走上层LLM兜底。
        _ABSTRACT_EXTREME = ("安全", "风险", "措施", "问题", "隐患", "方案", "建议", "原因",
                             "意义", "价值", "影响", "作用", "注意", "管理", "工作", "经验",
                             "挑战", "机会", "优势", "劣势", "趋势", "情况", "现状", "方向")
        _has_abstract = any(w in q for w in _ABSTRACT_EXTREME)
        _has_entity_ref = any(cn in q for cn in (D.get("entity_cn2en", {}) or {}))
        _has_attr_ref = any(cn in q for cn in (D.get("attr_cn2en", {}) or {}))
        if _has_abstract and not _has_entity_ref and not _has_attr_ref:
            return MISS_ANSWER
        sub = _entity_subset(q, D, data)  # 极值消歧(P1): 只在该实体类实例中求极值
        items = [(d, _num(_field(d, attr_en, aliases))) for d in sub.values()]
        items = [(d, v) for d, v in items if v is not None]
        if items:
            is_max = _is_max(q)
            # bug-C: 并列极值须全部列出, 不能只取 max/min 首个导致漏报并列者
            bestv = (max if is_max else min)(x[1] for x in items)
            best = [d for d, v in items if v == bestv]
            cname = attr_cn or cn2cn.get(attr_en, attr_en)
            _note_rows([(d, bestv) for d in best], cname)
            _names = "、".join(_display_name(d, aliases, default="") for d in best)
            return "%s的记录: %s (%s=%s)" % (("最大" if is_max else "最小") + cname, _names, cname, bestv)

    # ---- 平均 ----
    if attr_en and ("平均" in q or "均值" in q):
        sub = _entity_subset(q, D, data)  # 聚合作用域: 只在该实体类中平均
        vals = [_num(_field(d, attr_en, aliases)) for d in sub.values()]
        vals = [v for v in vals if v is not None]
        if vals:
            cname = attr_cn or cn2cn.get(attr_en, attr_en)
            _note_rows([(d, _num(_field(d, attr_en, aliases))) for d in sub.values()
                        if _num(_field(d, attr_en, aliases)) is not None],
                       cname, lambda d: _num(_field(d, attr_en, aliases)))
            return "%s平均值 %.2f (%d条)" % (cname, sum(vals)/len(vals), len(vals))

    # ---- 总和 ----
    if attr_en and any(k in q for k in ("总", "合计", "总和")):
        sub = _entity_subset(q, D, data)  # 聚合作用域: 只在该实体类中求和
        vals = [_num(_field(d, attr_en, aliases)) for d in sub.values()]
        vals = [v for v in vals if v is not None]
        if vals:
            cname = attr_cn or cn2cn.get(attr_en, attr_en)
            _note_rows([(d, _num(_field(d, attr_en, aliases))) for d in sub.values()
                        if _num(_field(d, attr_en, aliases)) is not None],
                       cname, lambda d: _num(_field(d, attr_en, aliases)))
            return "%s总和 %.2f" % (cname, sum(vals))

    # ---- 范围 (属性>N / <N / 在A到B) ----
    if attr_en:
        nums = _extract_nums(q)
        has_gt = any(k in q for k in ("大于", "高于", "超过", "以上"))
        has_lt = any(k in q for k in ("小于", "低于", "少于", "以下"))
        if ("到" in q or "之间" in q) and len(nums) >= 2:
            lo, hi = nums[0], nums[1]
            matched = [(d, _num(_field(d, attr_en, aliases))) for d in data.values()]
            cname = attr_cn or cn2cn.get(attr_en, attr_en)
            matched = _note_rows([(d, v) for d, v in matched if v is not None and lo <= v <= hi],
                                 cname)
            return "%s在%s到%s之间的(%d):\n%s" % (cname, lo, hi, len(matched), _fmt_names([_display_name(d, aliases, default="") for d, _ in matched]))
        elif has_gt and nums:
            n = nums[0]
            matched = [(d, _num(_field(d, attr_en, aliases))) for d in data.values()]
            cname = attr_cn or cn2cn.get(attr_en, attr_en)
            matched = _note_rows([(d, v) for d, v in matched if v is not None and v > n], cname)
            return "%s大于%s的(%d):\n%s" % (cname, n, len(matched), _fmt_names([_display_name(d, aliases, default="") for d, _ in matched]))
        elif has_lt and nums:
            n = nums[0]
            matched = [(d, _num(_field(d, attr_en, aliases))) for d in data.values()]
            cname = attr_cn or cn2cn.get(attr_en, attr_en)
            matched = _note_rows([(d, v) for d, v in matched if v is not None and v < n], cname)
            return "%s小于%s的(%d):\n%s" % (cname, n, len(matched), _fmt_names([_display_name(d, aliases, default="") for d, _ in matched]))

    # ---- 分组统计 ----
    if "统计" in q or "分组" in q or ("按" in q and "和" in q):
        grp_attrs = []
        parts = re.findall(r'按([^\s和]+)', q)
        for p in parts:
            en = D.get("attr_cn2en", {}).get(p.strip())
            if en:
                grp_attrs.append(en)
        # 处理 "按X和Y"
        if not grp_attrs and "和" in q and "按" in q:
            pieces = [x.strip() for x in q.split("和") if x.strip()]
            for p in pieces:
                p2 = p.replace("按", "")
                en = D.get("attr_cn2en", {}).get(p2)
                if en:
                    grp_attrs.append(en)
        if grp_attrs:
            cnt = defaultdict(int)
            for d in data.values():
                key = tuple(_field(d, a, aliases) or "?" for a in grp_attrs)
                cnt[key] += 1
            cn_grp = [cn2cn.get(a, a) for a in grp_attrs]
            return "按%s统计:\n%s" % ("和".join(cn_grp), "\n".join(f"  {'/'.join(k)}: {v}条" for k, v in cnt.items()))

    # ---- 反查: 哪些[属性]=值 ----
    if attr_en and ("哪些" in q or "是什么" in q):
        cname = attr_cn or cn2cn.get(attr_en, attr_en)
        matched = _note_rows([(d, _field(d, attr_en, aliases)) for d in data.values()
                              if _field(d, attr_en, aliases)], cname)
        return "%s信息:\n%s" % (cname, "\n".join(f"  {_display_name(d, aliases, default='')}: {v}" for d, v in matched[:20]))

    # ---- 属性值过滤计数 (泛化): "不锈钢波纹管的数量" / "船坞的数量" ----
    # data profiling 未覆盖的复合/取值过滤计数兜底（位于所有具名模板之后）。
    # 去掉调试后缀("单字段取值"), 给用户可读的计数回答。
    # 批 2 收紧(修编造): ① 极值问句(含"最X")不落到此兜底 —— 否则"设备中纯度最大是多少"
    # 会被答成"符合条件共 2 条"(与问题无关的计数, 评测记为编造); ② "裸实体计数"形式的
    # 跨域问句(如"有多少个催化剂"/"催化剂总数")不落到此兜底 —— 否则会命中数据里偶然同名的
    # 取值答成"符合条件共 1 条"。两种情况都该如实拒答。
    # 注意: 只拦"裸实体计数"这一形态, 不拦"X的数量"这类取值过滤(如"法兰的数量"),
    # 否则会误伤问句含取值词的正常过滤计数。
    _bare_entity_count = bool(re.search(r"(有多少个?|多少个|共多少|有多少)[\u4e00-\u9fff]{1,8}$", q)) \
        or bool(re.search(r"^[\u4e00-\u9fff]{1,8}总数\s*$", q))
    # 🔴 M15-8：问句点名了具体实体时，不能落到"取值过滤计数"兜底。
    #    症状：「AC-01 的投用年份是多少」答"符合条件共 319 条"——
    #    `_value_filter_count` 把 `AC-01` 当成一个"取值"去数有多少设备带这个值，
    #    于是数出全库条数。实测 v5 台账上「AC-01 的额定功率是多少」同样答 340 条。
    #
    #    根因与 M15-1（`-` 被当厂商名）是**同一类**：没把设备 ID 当实体，只当字符串。
    #    `_bare_entity_count` 那条拦不住，因为问句结尾是"是多少"不是纯数量问法。
    #
    #    ⚠️ 匹配要用**键里的编号段还原**：`data` 的键是 URI 形式（`AC_01_AssetData`、
    #    连字符被写成下划线），而问句里是 `AC-01`。直接拿 `_display_name` 比对是没用的
    #    —— AAS 视图下它返回空串（实测），守卫会永远失配。第一版就栽在这里。
    # ⚠️ 匹配必须**跨段重组**：`data` 的键是 URI 形式，而连字符被写成下划线，
    #    切分后是 `urn_ledger_AC_01_AssetData` → ['urn','ledger','AC','01','AssetData']，
    #    单段永远拼不回 `AC-01`。
    #
    #    🔴 判据必须是「**字母段 + 数字段**相邻」这个**正向**形状，
    #       不能反过来"过滤掉纯字母段"——设备编号的字母段（AC/TK/CV）本身就是纯字母，
    #       过滤掉它就把编号拆散了（实测 segs 变成 ['ledger','01','AssetData']，
    #       pair 全是 ledger-01 这种垃圾，永远匹配不上）。这个反向规则试了两版都失配。
    _named_entity = False
    _Q = re.sub(r"\s+", "", q)
    _PAIR = re.compile(r"^([A-Za-z]{2,4})(\d{1,4})$")
    for _nm in data:
        _segs = str(_nm).split("_")
        for _i in range(len(_segs) - 1):
            _m = _PAIR.match(_segs[_i] + _segs[_i + 1])
            if not _m:
                continue
            _letters, _digits = _m.group(1), _m.group(2)
            # 问句里三种写法都要认：AC-01 / AC_01 / AC01
            if ("%s-%s" % (_letters, _digits) in _Q
                    or "%s%s" % (_letters, _digits) in _Q
                    or "%s_%s" % (_letters, _digits) in _Q):
                _named_entity = True
                break
        if _named_entity:
            break
    if not _named_entity:
        for _cn, _uri in (get_entity_cn2uri() or {}).items():
            if _cn and len(_cn) >= 2 and _cn in q:
                _named_entity = True
                break
    if not _named_entity and not _EXTREME.search(q) \
            and not (is_cross_domain_data_query(q, D) and _bare_entity_count):
        _vc = _value_filter_count(q, data, with_names=True)
        if _vc:
            _names_vc = _vc[2] if len(_vc) > 2 else []
            _note_rows([(nm, data.get(nm)) for nm in _names_vc if nm in data], "取值匹配")
            return "符合条件共 %d 条" % _vc[0]

    # ---- 总数: 一共有多少条记录 ----
    if ("一共" in q or "总共有" in q or "总共" in q) and ("记录" in q or "多少" in q):
        # bug-A: out-of-KB 实体计数不得被静默答成全库总数。
        # 守卫复用 is_cross_domain_data_query/kb_vocab: 若该数据查询不含本KB任何领域词
        # (实体类/类型/状态/区域/属性), 说明问的是库外概念(如"多少个阀门") → 诚实提示, 不回全库条数。
        # 但"记录/数据"这类对本库全体记录的元查询(如"一共有多少条记录")仍答总数, 不误伤。
        if is_cross_domain_data_query(q, D) and not re.search(r'(记录|数据)', q):
            return "无相关数据，该知识库不含所问的实体概念"
        _note_rows([(k, d) for k, d in data.items()], "计数", lambda d: 1)
        return "一共有 %d 条记录" % len(data)

    return MISS_ANSWER


# ------------------------------------------------------------------ 统一问答信封(批 2)
# 未命中/无据话术标记（与批 1 评测口径一致：未命中 = 引擎拒答/无据，≠ 误答/编造）。
_MISS_MARKERS = MISS_MARKERS  # 单一真相源见 qa_constants.py(B3 收口)
# "裸实体计数/总数"问句形态（有多少个X / X总数）——此类问句若跨域或计数为 0，
# 说明库中并无该实体，应如实说未命中，而非回填"0"。
_BARE_COUNT_RE = re.compile(r"(有多少个?|多少个|共多少|有多少|总数)[\u4e00-\u9fff]{0,8}$")


def _miss_reason(q, text, data, D):
    """判定引擎输出是否为"未命中"(无据/拒答)。命中(有据)返回 None，未命中返回原因串。"""
    t = (text or "").strip()
    if not t:
        return "未命中：引擎无输出"
    for m in _MISS_MARKERS:
        if m in t:
            return "未命中：%s" % m
    # 空列举形态("无发电机"/"没有运行中")——仅在短答案时判，避免长答案里偶然带"无"
    if len(t) <= 40 and re.match(r"^(无|没有)[\u4e00-\u9fff]", t):
        return "未命中：库中无匹配记录"
    # 裸实体计数/总数且计数为 0 → 库中无此实体，视为未命中(不回填"0")
    if _BARE_COUNT_RE.search(q) and re.search(r"(?:总数|有)\s*0(?:\s*[台个条家批种艘本])?", t):
        return "未命中：库中无此实体记录(计数为 0)"
    # 裸实体计数/总数且跨域(问句不含本 KB 任何领域词) → 该知识库不含所问实体
    if _BARE_COUNT_RE.search(q) and is_cross_domain_data_query(q, D):
        return "未命中：该知识库不含所问的实体或概念"
    return None


def answer_envelope(q, data, D):
    """统一问答信封（批 2）：返回 {answer, hit, reason, evidence[], advisory?}。

    设计约束：
    - **所有分支都带 hit**（含引擎异常分支）。
    - `hit=false` 时 `answer` 明示"未命中"并给 `reason`，**绝不回填任何推测内容**。
    - 每条 evidence 可回溯：{kind: entity|relation|dict|doc, id, label, source}。
    - **向后兼容**：本函数是新增层，`answer()` 的字符串返回与老字段一律不改；
      老调用方(api_server / eval_qa)读取行为不变。
    - 异常**不静默吞**：引擎异常写入 reason/answer 原文。
    """
    try:
        text, calc = answer_traced(q, data, D)
    except Exception as e:  # 引擎异常按未命中处理，但原因如实露出，不掩盖
        return {"answer": "未命中：问答引擎内部异常，无法给出可靠结果。（%s）" % e,
                "hit": False, "reason": "引擎异常：%s" % e,
                "evidence": [], "advisory": None, "raw_answer": "", "source": "error"}
    reason = _miss_reason(q, text, data, D)
    if reason:
        return {"answer": "未命中：%s。可以换个问法，或确认该信息是否已录入本知识库。"
                          % reason.split("：", 1)[-1],
                "hit": False, "reason": reason, "evidence": [], "advisory": None,
                "raw_answer": text, "source": "refuse"}
    # 命中后必须能回溯：既不是拒答，也要能在本体数据里指出答案的出处。
    # 老逻辑只看字符串哨兵，证据为空照样算命中——"找到相关实体: checkedBy、producedIn"
    # 这类占位文本就是这么混进来的。现在证据为空 = 未验证，不计命中。
    try:
        from evidence import build_trace
        ev = build_trace(q, data, D, text, trace=calc)
    except Exception as e:  # 证据提取异常不静默吞：写进 reason 供排查，answer 仍返回
        ev = []
        err = str(e)
    else:
        err = ""
    if not ev:
        return {"answer": text, "hit": False,
                "reason": ("命中但无法回溯：答案未落在本体数据上" if not err
                           else "证据提取异常：%s" % err),
                "evidence": [], "advisory": None, "raw_answer": text,
                "source": "unverified"}
    return {"answer": text, "hit": True, "reason": "命中：规则检索有据",
            "evidence": ev, "advisory": None, "raw_answer": text, "source": "rule",
            "trace_total": (calc or {}).get("total", 0),
            "trace_truncated": bool((calc or {}).get("truncated"))}


# ------------------------------------------------------------------ main

def main():
    if len(sys.argv) < 3:
        print("用法: python ontology_qa_v3.py <nt文件> '<问题>' [lexicon.json]")
        sys.exit(1)
    nt_file = sys.argv[1]
    question = sys.argv[2]
    lex = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "config", "lexicon.json")
    D = load_dict(lex)
    triples = parse_nt(nt_file)
    data = build_data(triples, D)
    if not data:
        print("本体解析失败或无实例")
        sys.exit(1)
    print(answer(question, data, D))


if __name__ == "__main__":
    main()
