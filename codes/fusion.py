import re

import safe_log
# codes/fusion.py
"""工厂本体问答 - 融合决策层。纯标准库。"""

_RULE_SOURCES = {'rule', 'logical'}
_GRAPH_SOURCES = {'graph'}
_TEXT_SOURCES = {'bm25', 'vector', 'hybrid'}
_DOC_SOURCES = {'doc'}
_NO_DATA_ANSWER = '无相关数据（该知识库不含该实体概念）'

# 候选来源权重(集中一处, 取代散落在 api_server 构造处的魔法数 0.9/1.0/0.5)。
# 用途: 候选入队时的 score; 只影响 doc/graph/text 之间的相对先后, rule/logical 由 _RULE_SOURCES 优先。
CAND_SCORE = {'rule': 1.0, 'logical': 1.0, 'doc': 0.9, 'graph': 1.0,
              'bm25': 0.5, 'vector': 0.5, 'hybrid': 0.5}
# doc 候选相关性下限(> 阈值才算相关)。0.0 = 只要过二元组闸门即相关(原行为)。
DOC_MIN_SCORE = 0.0
# 拒绝文案对外公开名(与 api_server 共用单一来源, 防双源漂移)。
NO_DATA_ANSWER = _NO_DATA_ANSWER


def _valid(c):
    return isinstance(c, dict) and c.get('answer')


def _ev(c):
    e = c.get('evidence')
    return e if isinstance(e, list) else []


def _dedup_evidence(evs):
    seen = set()
    out = []
    for e in evs:
        if not isinstance(e, dict):
            continue
        key = (e.get('entity'), e.get('class'), e.get('iri'), e.get('attr'),
               e.get('value'), e.get('source'), e.get('rule_id'))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _doc_relevant(c):
    """doc 候选相关性: score 需 > DOC_MIN_SCORE 且 evidence 非空。"""
    try:
        s = float(c.get('score', 0))
    except (TypeError, ValueError):
        s = 0.0
    return s > DOC_MIN_SCORE and bool(_ev(c))


def _pick_main(cands):
    """按优先级选主候选: rule/logical > doc(相关) > graph > bm25/vector/hybrid。"""
    for c in cands:
        if c.get('source') in _RULE_SOURCES and _ev(c):
            return c
    for c in cands:
        if c.get('source') in _DOC_SOURCES and _doc_relevant(c):
            return c
    for c in cands:
        if c.get('source') in _GRAPH_SOURCES:
            return c
    for c in cands:
        if c.get('source') in _TEXT_SOURCES:
            return c
    return None


def _answer_tokens(text):
    """答案里可以拿去做核对的硬信息：数字与编号。"""
    t = text or ''
    nums = re.findall(r'-?\d+(?:\.\d+)?', t)
    ids = re.findall(r'[A-Za-z]{1,8}[-_]?\d{1,8}', t)
    return [x for x in nums] + [x for x in ids if not any(x in n for n in nums)]


def _coverage(answer, evidence):
    """答案里的数字/编号有多少能在证据里找到。无可核对信息时返回 None。

    这是"置信度"唯一靠谱的来源：不看候选来自哪个引擎，只看答案自身能不能被证据撑住。
    按来源给 high（旧行为）会把"规则引擎算错了"也标成高置信，等于没有置信度。
    """
    toks = _answer_tokens(answer)
    if not toks:
        return None
    blob = ' '.join(
        str(e.get(k) if e.get(k) is not None else '')
        for e in evidence if isinstance(e, dict)
        for k in ('value', 'label', 'entity', 'iri', 'quote', 'text', 'id')
    )
    hit = sum(1 for t in toks if t in blob)
    return hit / float(len(toks))


def fuse(question, candidates, cross_domain=False, kb='', schema=None):
    if cross_domain:
        return {'ok': True, 'mode': 'cross_domain', 'answer': _NO_DATA_ANSWER,
                'evidence': [], 'engines': [], 'structured': None,
                'no_basis': True, 'confidence': 'none', 'kb': kb}

    cands = [c for c in (candidates or []) if _valid(c)]
    engines = []
    for c in cands:
        # 候选可用 engines 字段声明真实命中的引擎(如 hybrid → [bm25, vector]);
        # 未声明则回落到 source, 保证与 api_server 的 engines 词汇一致(P2-6)。
        declared = c.get('engines') if isinstance(c.get('engines'), list) else None
        for s in (declared or [c.get('source')]):
            if s and s not in engines:
                engines.append(s)

    if not cands:
        return {'ok': True, 'mode': 'none', 'answer': '', 'evidence': [],
                'engines': engines, 'structured': None,
                'no_basis': True, 'confidence': 'none', 'kb': kb}

    main = _pick_main(cands)
    if main is None:
        # 有 answer 但无任何可识别 source 的候选: 取第一个, 但不得 high
        main = cands[0]

    main_ev = _ev(main)
    merged = list(main_ev)
    for c in cands:
        if c is main:
            continue
        merged.extend(_ev(c))
    merged = _dedup_evidence(merged)
    # schema 透传: 有 schema 时给合并证据补 IRI(IRI 溯源); 无 schema 保持原样。
    if schema:
        try:
            import evidence_norm
            merged = evidence_norm.normalize_evidence(merged, schema)
        except Exception as exc:
            safe_log.swallow('fusion.normalize', exc)

    answer = main.get('answer') or ''
    if not answer:
        return {'ok': True, 'mode': main.get('source') or 'none', 'answer': '',
                'evidence': merged, 'engines': engines,
                'structured': main.get('structured'), 'no_basis': True,
                'confidence': 'none', 'kb': kb}

    cov = _coverage(answer, main_ev)
    if not main_ev:
        confidence = 'low'
    elif cov is None:
        # 答案里没有数字/编号可核（如"已入库"这类枚举值），只能按是否有证据判
        confidence = 'high' if main.get('source') in _RULE_SOURCES else 'medium'
    elif cov >= 0.9:
        confidence = 'high'
    elif cov >= 0.5:
        confidence = 'medium'
    else:
        confidence = 'low'

    no_basis = (not main_ev) or (cov == 0.0)
    return {'ok': True, 'mode': main.get('source') or 'none', 'answer': answer,
            'evidence': merged, 'engines': engines,
            'structured': main.get('structured'), 'no_basis': no_basis,
            'confidence': confidence, 'coverage': cov, 'kb': kb}


if __name__ == '__main__':
    # 1) 规则 high
    r = fuse('q', [{'answer': 'A', 'source': 'rule', 'score': 0.9,
                    'evidence': [{'entity': 'E', 'attr': 'a', 'value': 'A',
                                  'source': 'rule', 'rule_id': 'r1'}],
                    'structured': {'x': 1}}])
    assert r['ok'] and r['confidence'] == 'high' and r['answer'] == 'A'
    assert r['mode'] == 'rule' and r['engines'] == ['rule'] and not r['no_basis']

    # 2) 有 answer 但无证据 → low
    r = fuse('q', [{'answer': 'B', 'source': 'graph', 'score': 0.5,
                    'evidence': [], 'structured': None}])
    # 无证据 → no_basis=True(原 graph 路径 no_basis = not g_ev 的语义, 防"看似有据实则空答")
    assert r['confidence'] == 'low' and r['answer'] == 'B' and r['no_basis']

    # 3) 空 answer → none
    r = fuse('q', [{'answer': '', 'source': 'bm25', 'score': 0.3,
                    'evidence': [{'entity': 'E', 'source': 'bm25'}],
                    'structured': None}])
    assert r['no_basis'] and r['confidence'] == 'none' and r['answer'] == ''

    # 4) cross_domain 拒答
    r = fuse('q', [{'answer': 'X', 'source': 'rule', 'score': 1.0,
                    'evidence': [{'entity': 'E', 'source': 'rule'}],
                    'structured': None}], cross_domain=True)
    assert r['no_basis'] and r['confidence'] == 'none'
    assert r['answer'] == _NO_DATA_ANSWER and r['evidence'] == []

    # 5) 空输入稳健
    r = fuse('q', None)
    assert r['no_basis'] and r['confidence'] == 'none'

    # 6) 优先级: doc(相关) > graph, 证据合并去重
    ev_g = {'entity': 'E', 'attr': 'a', 'value': 'V', 'source': 'graph'}
    r = fuse('q', [
        {'answer': 'G', 'source': 'graph', 'score': 0.9, 'evidence': [ev_g], 'structured': None},
        {'answer': 'D', 'source': 'doc', 'score': 0.4,
         'evidence': [{'entity': 'E', 'attr': 'a', 'value': 'V', 'source': 'doc'}], 'structured': None},
    ])
    assert r['answer'] == 'D' and r['mode'] == 'doc'
    assert r['engines'] == ['graph', 'doc']
    assert len(r['evidence']) == 2

    # 7) doc 不相关(score=0) → 落到 graph
    r = fuse('q', [
        {'answer': 'D', 'source': 'doc', 'score': 0.0, 'evidence': [], 'structured': None},
        {'answer': 'G', 'source': 'graph', 'score': 0.5, 'evidence': [ev_g], 'structured': None},
    ])
    assert r['answer'] == 'G' and r['mode'] == 'graph'

    # 8) 置信度按证据覆盖率：答案里的数字能在证据里找到 -> high
    r = fuse('q', [{'answer': '共 42 个', 'source': 'rule', 'score': 1.0,
                    'evidence': [{'entity': 'Product', 'attr': 'count',
                                  'value': '42', 'source': 'rule'}],
                    'structured': None}])
    assert r['confidence'] == 'high' and r['coverage'] == 1.0 and not r['no_basis']

    # 9) 数字在证据里找不到 -> low，且判为无据（规则引擎同样不给面子）
    r = fuse('q', [{'answer': '共 99 个', 'source': 'rule', 'score': 1.0,
                    'evidence': [{'entity': 'Product', 'attr': 'count',
                                  'value': '42', 'source': 'rule'}],
                    'structured': None}])
    assert r['confidence'] == 'low' and r['coverage'] == 0.0 and r['no_basis']

    # 10) 部分覆盖 -> medium
    r = fuse('q', [{'answer': 'A 12 个，B 34 个', 'source': 'graph', 'score': 1.0,
                    'evidence': [{'entity': 'A', 'attr': 'n', 'value': '12',
                                  'source': 'graph'}],
                    'structured': None}])
    assert r['confidence'] == 'medium' and r['coverage'] == 0.5

    print('fusion.py self-test OK')