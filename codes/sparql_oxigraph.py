#!/usr/bin/env python3
"""sparql_oxigraph.py — 可选 SPARQL 1.1 后端（不改变零依赖默认档）

为什么不默认启用：
    默认档坚持零第三方依赖、离线可跑，自研的 SPARQL 子集够覆盖台账问答。
    需要完整 SPARQL 1.1（UNION / OPTIONAL / 聚合 / LIMIT）时，用这个适配器接 Oxigraph。

用法：
    pip install pyoxigraph          # 客户环境自行决定
    from sparql_oxigraph import query
    query("codes/output/valve.nt", "SELECT ... LIMIT 5")

未安装时抛 OxigraphUnavailable，调用方据此提示"该查询需要 1.1 后端"，
不要静默退回子集（子集会给出解析错误，看起来像查询写错）。
"""
from __future__ import annotations

from typing import List


class OxigraphUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import pyoxigraph  # noqa: F401
        return True
    except Exception:
        return False


def query(nt_path: str, sparql: str, fmt: str = "nt") -> List[dict]:
    """执行查询并返回 [{var: value}]。用 N-Triples 文件载入一个内存 store。"""
    if not available():
        raise OxigraphUnavailable(
            "pyoxigraph 未安装：完整 SPARQL 1.1 需要它，默认零依赖档用 ontology_sparql 子集")
    from pyoxigraph import NamedNode, QuerySolutions, RdfFormat, Store

    store = Store()
    with open(nt_path, "rb") as f:
        store.bulk_load(f, format=RdfFormat.N_TRIPLES)
    sol = store.query(sparql)
    if not isinstance(sol, QuerySolutions):
        return []
    rows = []
    for binding in sol:
        rows.append({k.value: (v.value if hasattr(v, "value") else str(v))
                     for k, v in binding.items()})
    return rows
