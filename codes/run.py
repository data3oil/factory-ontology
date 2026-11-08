#!/usr/bin/env python3
"""run.py — 现场一站式入口（交付包核心，路径全相对，零硬编码）。

在交付包内任意位置运行，路径基于脚本自身位置自适应。
纯标准库，无第三方依赖（LLM 兜底需 requests + 本地 Ollama，可选）。

用法:
  python run.py setup <数据文件> [表名]   # 自动建模(本体+词典+验证)，支持 csv/json/sqlite/xlsx
  python run.py ask "<问题>"              # 交互问答(规则+LLM兜底)
  python run.py test                       # 自检示例数据
  python run.py plugin list                # 生态插件: list/run/ext/install/remove
"""

import os
import re
import sys
import json

__version__ = "0.5.0"
import importlib.util

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from qa_constants import MISS_ANSWER  # noqa: E402  未命中哨兵单一真相源(B3 收口)
from lexicon import merge_lexicon  # noqa: E402  词典合并规则单一实现
OUT = os.path.join(ROOT, "output")
CFG = os.path.join(ROOT, "config")
STATE = os.path.join(ROOT, "current.json")

# 向量混合检索惰性缓存(按 nt 路径隔离, 切库自动重建; 与 api_server 逻辑一致)
_GRAPH_CACHE = {}
_BM25_CACHE = {}
_VECTOR_CACHE = {}


def _load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _name(source_path):
    return os.path.splitext(os.path.basename(source_path))[0]


def _rel_or_abs(p, base):
    """相对路径，跨盘（Windows C:/D: 不同挂载）不可计算时回退绝对路径。

    自助建模允许选**仓库外/异盘**数据目录（界面「外部数据目录」），
    此时 os.path.relpath 会抛 ValueError('path is on mount C:, start on mount D:')，
    曾导致 confirm 建本体失败（报「按确认 schema 建本体失败」，真因被掩盖）。
    """
    try:
        return os.path.relpath(p, base)
    except ValueError:
        return os.path.abspath(p)


def _guard_overwrite(nt, table, kind):
    """建本体前保护已有产物。

    单表建模和 schema 多表建模写的是同一个 `output/<表名>.nt`。台账一多表，
    单表那条命令就把关系边悄悄删掉了——产物还在、问答还"有答案"，只是边没了，
    表现为「关系问句答成属性名」，极难查。所以换建法覆盖前先备份并明说。
    """
    if not os.path.exists(nt):
        return
    prev = None
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE, encoding="utf-8"))
        except (ValueError, OSError):
            prev = None
    if not isinstance(prev, dict) or prev.get("table") != table:
        return
    prev_kind = "schema" if prev.get("schema") else "single"
    if prev_kind == kind:
        return
    import shutil
    import time as _time
    bak = nt + ".bak-" + _time.strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copy2(nt, bak)
    except OSError:
        return
    label = {"schema": "多表 schema 建模", "single": "单表建模"}
    print(f"⚠️ {os.path.basename(nt)} 原为{label[prev_kind]}产物，本次是{label[kind]}，"
          f"覆盖前已备份到 {os.path.basename(bak)}")


def setup_schema(data_dir, schema_path, table="factory", force=False):
    """schema 驱动统一建模（激进重构核心，替代 csv_to_owl/multi_table）。

    从多表数据目录 + ontology_schema.json 建统一本体（schema_ontology.to_nt），
    输出标准 N-Triples 供下游问答/图检索消费。复用优先·极简落地：
    - 复用 schema_ontology 已验证的 schema 驱动建模内核（sme 精髓）
    - 统一建本体职责到 schema_ontology，消除 csv_to_owl/multi_table 重复

    用法:
      python run.py setup-schema <数据目录> [schema.json] [表名]
    """
    os.makedirs(OUT, exist_ok=True)
    so = _load("schema_ontology", os.path.join(ROOT, "schema_ontology.py"))
    schema_path = schema_path or os.path.join(CFG, "ontology_schema.json")
    nt = os.path.join(OUT, f"{table}.nt")

    print(f"\n[工厂智能体] schema 驱动建模: {table}")
    # 每步失败都报告清晰原因，不裸抛异常
    try:
        print(f"[1/3] 加载多表数据 {data_dir}")
        data = so.load_all(data_dir)
        print(f"      -> {len(data)} 表: {list(data.keys())}")
    except Exception as e:
        print(f"❌ 本体建模失败（步骤1/3 加载数据）: {e}")
        return None, None

    try:
        if not os.path.exists(schema_path):
            print(f"❌ 本体建模失败（步骤2/3）: 无 schema 文件 {schema_path}，需提供 ontology_schema.json")
            return None, None
        print(f"[2/3] 加载 schema + 约束校验")
        schema = so.load_schema(schema_path)
        issues = so.validate(data, schema)
        errors = [i for i in issues if i.get("severity") == "error"]
        if issues:
            print(f"⚠️ 约束校验 {len(issues)} 问题:", [i["msg"] for i in issues[:3]])
        if errors and not force:
            # fail-closed：主键重复这类错误建出来的库，问答会"有答案"但答案是错的，
            # 比报错难查得多。默认停下，确需强建加 --force。
            print(f"❌ 约束校验发现 {len(errors)} 个必须修复的问题，已停止建库"
                  f"（{errors[0]['msg']}）。确认仍要建库请加 --force")
            return None, None
    except Exception as e:
        print(f"❌ 本体建模失败（步骤2/3 加载 schema）: {e}")
        return None, None

    try:
        print(f"[3/3] 建统一本体 -> {os.path.basename(nt)}")
        _guard_overwrite(nt, table, "schema")
        lines = so.to_nt(data, schema, outpath=nt)
        if not os.path.exists(nt):
            raise RuntimeError("to_nt 未生成本体文件")
        graph = so.build_graph(data, schema)
        model = so.build_ontology_model(data, schema)
        print(f"✅ 本体: {len(lines)} 行 N-Triples | 图 {len(graph['nodes'])} 节点/{len(graph['edges'])} 边")
        print(f"   类型体系: {[h['name'] for h in model['type_hierarchy']]}")
        print(f"   语义域: {model['semantic_domains']}")
    except Exception as e:
        print(f"❌ 本体建模失败（步骤3/3 建本体）: {e}")
        return None, None

    # 同一份 schema 顺手生成/合并词典：否则 ask() 会自己重建一份，
    # 把人工确认过的属性名和 review.pending 全冲掉（config/lexicon_factory.json
    # 就被这样冲掉过 240 行）。
    lex_path = os.path.join(CFG, f"lexicon_{table}.json")
    try:
        import multi_model as mm
        fresh = mm._build_lexicon(schema, data)
        old_lex = None
        if os.path.exists(lex_path):
            try:
                old_lex = json.load(open(lex_path, encoding="utf-8"))
            except (ValueError, OSError):
                old_lex = None
            if old_lex is not None:
                import shutil
                import time as _time
                try:
                    shutil.copy2(lex_path, lex_path + ".bak-" + _time.strftime("%Y%m%d-%H%M%S"))
                except OSError:
                    pass
        if isinstance(old_lex, dict):
            fresh = merge_lexicon(old_lex, fresh)
        with open(lex_path, "w", encoding="utf-8") as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
        print(f"   词典: {os.path.basename(lex_path)}（{len(fresh.get('attr_cn2en') or {})} 个属性名）")
    except Exception as e:      # 词典失败不挡建库，ask 时还能重建
        print(f"   ⚠️ 词典生成失败（不影响本体）: {e}")
        lex_path = lex_path if os.path.exists(lex_path) else None

    state = {"nt": _rel_or_abs(nt, ROOT), "schema": _rel_or_abs(schema_path, ROOT),
             "data_dir": _rel_or_abs(data_dir, ROOT), "table": table}
    if lex_path:
        state["lexicon"] = _rel_or_abs(lex_path, ROOT)
    json.dump(state, open(STATE, "w", encoding="utf-8"))
    return nt, lex_path


def setup(source_path, table=None, use_llm=True):
    os.makedirs(OUT, exist_ok=True)
    table = table or _name(source_path)
    nt = os.path.join(OUT, f"{table}.nt")
    lex = os.path.join(CFG, f"lexicon_{table}.json")

    print(f"\n[工厂智能体] 建模: {table}")
    _guard_overwrite(nt, table, "single")
    print(f"[1/3] 转本体 {source_path}")
    csv2owl = _load("csv_to_owl", os.path.join(ROOT, "csv_to_owl.py"))
    old = sys.argv
    # 若有对象属性配置则传入 --relations
    rel_path = os.path.join(CFG, "relations.json")
    rel_args = ["--relations", rel_path] if os.path.exists(rel_path) else []
    sys.argv = ["csv_to_owl", source_path, nt] + rel_args
    try:
        csv2owl.main()
    except SystemExit:
        pass
    sys.argv = old
    if not os.path.exists(nt):
        print("❌ 本体生成失败"); return None, None

    print("[2/3] 自动词典 (LLM推断字段语义)...")
    # 先备份再生成：词典里有人的劳动（人工确认过的中文名、review.pending），
    # 不能每次 setup 都当草稿纸覆盖。
    old_lex = None
    if os.path.exists(lex):
        try:
            old_lex = json.load(open(lex, encoding="utf-8"))
        except (ValueError, OSError):
            old_lex = None
        import shutil
        import time as _time
        try:
            shutil.copy2(lex, lex + ".bak-" + _time.strftime("%Y%m%d-%H%M%S"))
        except OSError:
            pass
    from agents.lexicon_agent import LexiconAgent
    r = LexiconAgent().run({"source_csv": source_path, "out_lexicon": lex,
                            "use_llm": use_llm, "table_name": table,
                            "merge_into": old_lex})
    if not r.ok:
        print(f"⚠️ 词典: {r.error}")
    if not os.path.exists(lex):
        print("❌ 词典未生成"); return None, None

    # Agent 已按 merge_into 合并过的不用再合；没支持的走这里兜一层
    if old_lex is not None:
        try:
            fresh = json.load(open(lex, encoding="utf-8"))
        except (ValueError, OSError):
            fresh = None
        if isinstance(fresh, dict) and fresh.get("merged_from_previous") is not True:
            merged = merge_lexicon(old_lex, fresh)
            merged["merged_from_previous"] = True
            with open(lex, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            print("   词典已合并旧条目（旧值优先，冲突记入 review.pending）")

    # 存状态 + 概要（存相对路径，包可整体迁移，不绑定本机盘符）
    json.dump({"nt": os.path.relpath(nt, ROOT), "lexicon": os.path.relpath(lex, ROOT),
               "table": table}, open(STATE, "w", encoding="utf-8"))
    D = json.load(open(lex, encoding="utf-8"))
    print("[3/3] 建模完成 ✅")
    print("\n=== 词典概要(请人工确认关键字段中文名是否合理) ===")
    for k, v in list(D.get("attr_cn2en", {}).items())[:10]:
        print(f"  {v} = {k}")

    return nt, lex


def _ensure_lexicon(st):
    """确保 lexicon 文件存在。若 current.json 指向的词典不存在(如被清理),
    从 data_dir 用 suggest_schema 自动重建(复用 multi_model._build_lexicon), 更新 current.json。
    返回 lexicon 绝对路径。失败则返回 None(调用方降级)。"""
    lex = st.get("lexicon")
    if lex:
        lex_abs = lex if os.path.isabs(lex) else os.path.join(ROOT, lex)
        if os.path.exists(lex_abs):
            return lex_abs
    # 词典不存在 → 重建（优先从 data_dir, 否则从 nt 解析实体/属性）
    try:
        mm = _load("multi_model", os.path.join(ROOT, "multi_model.py"))
        so = _load("schema_ontology", os.path.join(ROOT, "schema_ontology.py"))
        lexicon = None
        # 路径1: data_dir 重建
        data_dir = st.get("data_dir")
        if data_dir:
            data_dir_abs = data_dir if os.path.isabs(data_dir) else os.path.join(ROOT, data_dir)
            if os.path.isdir(data_dir_abs):
                data = so.load_all(data_dir_abs)
                schema = so.suggest_schema(data)
                lexicon = mm._build_lexicon(schema, data)
        # 路径2: 从 nt 解析实体/属性重建(不依赖 data_dir)
        if not lexicon:
            nt = st.get("nt")
            if nt:
                nt_abs = nt if os.path.isabs(nt) else os.path.join(ROOT, nt)
                if os.path.exists(nt_abs):
                    from ontology_qa_v3 import parse_nt as _parse
                    triples = _parse(nt_abs)
                    attrs = set()
                    for s, p, o in triples:
                        p_ = str(p)
                        if "rdf-schema#label" in p_ and "class" not in str(o).lower():
                            continue
                        # 数据属性: 对象是字面量
                        if not str(o).startswith(("http://", "https://")):
                            a = str(p).split("#")[-1].split("/")[-1].strip("<>")
                            if a and not a.startswith("rdf") and a not in ("type", "label"):
                                attrs.add(a)
                    schema = {"entities": [{"id": "Auto", "attributes": [{"name": a} for a in attrs]}]}
                    lexicon = mm._build_lexicon(schema, {})
        if lexicon:
            table = st.get("table", "auto")
            lex_path = os.path.join(CFG, f"lexicon_{table}.json")
            with open(lex_path, "w", encoding="utf-8") as f:
                json.dump(lexicon, f, ensure_ascii=False, indent=2)
            st["lexicon"] = os.path.relpath(lex_path, ROOT)
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=2)
            return lex_path
    except Exception:
        pass
    return None


def ask(question, nt=None, lex=None):
    if not os.path.exists(STATE):
        print("请先运行: python run.py setup <数据文件>")
        return
    st = json.load(open(STATE, encoding="utf-8"))
    nt = nt or st["nt"]
    # setup-schema 不写 lexicon, 缺省用 config/lexicon.json; 词典不存在则自动重建
    lex = lex or st.get("lexicon") or os.path.join(CFG, "lexicon.json")
    lex_abs = _ensure_lexicon(st)
    if not lex_abs:
        print("词典不可用，请重新建模"); return
    lex = lex_abs
    # 兼容相对/绝对路径：相对则基于包根 ROOT 解析（迁移后路径自动重定位）
    nt = nt if os.path.isabs(nt) else os.path.join(ROOT, nt)
    lex = lex if os.path.isabs(lex) else os.path.join(ROOT, lex)
    v3 = _load("ontology_qa_v3", os.path.join(ROOT, "ontology_qa_v3.py"))
    D = v3.load_dict(lex)
    data = v3.build_data(v3.parse_nt(nt), D)
    if not data:
        print("本体无实例"); return
    ans = v3.answer(question, data, D)
    if ans == MISS_ANSWER:
        # 图检索兜底(与 API 层一致): 开放式/关系问题走 GraphRAG
        from graph_rag import answer_graph, build_graph
        gans, _ = answer_graph(question, nt)
        if not gans.startswith("[图检索]"):
            print(gans)
        else:
            _ask_hybrid(question, nt, D, gans)
    else:
        print(ans)


def _ask_hybrid(question, nt, D, gans):
    """混合检索(BM25 稀疏 + 向量语义)兜底，与 api_server 逻辑一致。

    检索链路: 规则 → GraphRAG → BM25(稀疏) → 向量语义(embedding) → 融合 → miss。
    向量层召回语义相近实体(如"最贵"→price、"油轮"→船型)；embedding 失败回落 BM25/图检索，绝不阻塞。
    """
    try:
        from bm25_retrieval import BM25Index
        from vector_retrieval import VectorIndex
        from graph_rag import build_graph
        if nt not in _GRAPH_CACHE:
            _GRAPH_CACHE[nt] = build_graph(nt)[0]
        graph = _GRAPH_CACHE[nt]
        if nt not in _BM25_CACHE:
            _BM25_CACHE[nt] = BM25Index.from_graph(graph)
        if nt not in _VECTOR_CACHE:
            _VECTOR_CACHE[nt] = VectorIndex.from_graph(graph, lexicon=D)
        bm_hits = _BM25_CACHE[nt].search(question, top_k=3, min_score=4.0)
        # 向量语义召回: 仅强语义信号(min_score 0.60)才触发, 避免对无关问题误召回
        vec_hits = _VECTOR_CACHE[nt].search(question, top_k=5, min_score=0.60)
        # 融合: BM25 + 向量取并集(去重), 保持 BM25 优先排序
        seen, fused = set(), []
        for h in (bm_hits + vec_hits):
            ent = h["entity"]
            if ent not in seen:
                seen.add(ent)
                fused.append(h)
        if fused:
            # 别把内部标识当答案："Valve_products、sizeMm" 是类名和字段名，
            # 用户看到这行只知道系统没答上来，却不知道自己在看什么。
            names = []
            for h in fused[:8]:
                seg = str(h["entity"]).rsplit("_", 1)[-1]
                if re.search(r"\d", seg) or any("\u4e00" <= ch <= "\u9fff" for ch in seg):
                    if seg not in names:
                        names.append(seg)
            if names:
                print("没能直接答上来。可能相关的记录：%s。" % "、".join(names[:6]))
                print("可以换个问法，比如带上属性名（价格 / 数量 / 日期 / 状态）。")
                return
    except Exception:
        pass
    # 混合检索未命中/不可用 → 回落图检索兜底结果
    print(gans)


def self_test():
    """自检: 用示例数据跑通"建模 → 问答"，并逐题断言，不只是打印。

    以前这里问的是「运行中的设备 / 空压机 / 功率最大的设备」——ai4i 里根本没有
    这几个字段，自检每次都打印「有 0 台运行中的 / 无空压机 / 未定位到相关实体」。
    自检自己的问题答不上来，等于没有自检。现在问数据里真有的字段，答不上来就退出码 1。
    """
    data_csv = os.path.join(ROOT, "data", "ai4i.csv")
    if not os.path.exists(data_csv):
        print("无示例数据 data/ai4i.csv, 跳过自检")
        return 0
    print("=== 自检: 示例设备数据 (UCI AI4I 预测性维护) ===")
    # 用 README 里的表名 ai4i：自检要跑用户真会跑的那条路径。
    # 换表名会连词典里的实体计数词一起换（entity_cn2en 由表名词干生成），
    # 自检若自造表名，测的就不是文档里的路了。
    saved = None
    if os.path.exists(STATE):
        try:
            saved = open(STATE, encoding="utf-8").read()
        except OSError:
            saved = None
    try:
        nt, lex = setup(data_csv, "ai4i", use_llm=False)
        if not nt or not lex:
            print("❌ 自检失败: 建模未完成")
            return 1
        v3 = _load("ontology_qa_v3", os.path.join(ROOT, "ontology_qa_v3.py"))
        D = v3.load_dict(lex)
        data = v3.build_data(v3.parse_nt(nt), D)
        checks = [
            ("有多少台机器", "10000"),
            ("空气温度最高的机器", "空气温度"),
            ("刀具磨损最大的机器", "刀具磨损"),
        ]
        print()
        bad = 0
        for q, must in checks:
            ans = v3.answer(q, data, D)
            ok = ans != MISS_ANSWER and must in ans
            bad += 0 if ok else 1
            print(f"{'✅' if ok else '❌'} 问: {q}\n   {ans}")
        print(f"\n自检结果: {len(checks) - bad}/{len(checks)} 通过")
        return 0 if bad == 0 else 1
    finally:
        if saved is not None:
            with open(STATE, "w", encoding="utf-8") as f:
                f.write(saved)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    if args[0] == "setup":
        if len(args) < 2:
            print("用法: python run.py setup <数据文件> [表名]"); return
        setup(args[1], args[2] if len(args) > 2 else None)
    elif args[0] == "setup-schema":
        rest = [a for a in args[1:] if a != "--force"]
        force = "--force" in args
        if not rest:
            print("用法: python run.py setup-schema <数据目录> [schema.json] [表名] [--force]")
            return
        setup_schema(rest[0], rest[1] if len(rest) > 1 else None,
                     rest[2] if len(rest) > 2 else "factory", force=force)
    elif args[0] == "ask":
        if len(args) < 2:
            print("用法: python run.py ask '<问题>'"); return
        ask(args[1])
    elif args[0] == "test":
        sys.exit(self_test())
    elif args[0] == "plugin":
        # 生态插件：list/run/ext/install/remove（见 plugin_framework.cmd_plugin）
        from plugin_framework import cmd_plugin
        return cmd_plugin(args[1:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
