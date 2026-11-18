#!/usr/bin/env python3
"""prebuild_nt.py — 按 kbs.json 预建 output/*.nt（评测门的输入）。纯标准库。

为什么需要它
    verify_qa_envelope / eval_qa / verify_cq_baseline / eval_holdout 直接读
    kbs.json 里的 nt 路径。干净检出（新 clone、CI 首次跑）没有 output/*.nt
    （生成物不入库），这些门会直接 FileNotFoundError。
    本机因为跑过建模所以一直有文件——典型的"本地绿、CI 红"。

    所以凡是"门里有评测、评测读 nt"的场景，跑门之前先跑本脚本。

它**不做什么**
    · 不写词典（multi_model.build 会覆盖 config/lexicon_*.json，CI 里绝不能碰）
    · 不改 kbs.json 注册表
    · 不调 LLM（suggest_schema 强制 use_llm=False，保证可重复）

用法
    python scripts/prebuild_nt.py              # 建全部已注册库
    python scripts/prebuild_nt.py valve chem   # 只建指定库
    python scripts/prebuild_nt.py --check      # 只报告缺哪些，不建
    python scripts/prebuild_nt.py --list       # 列出会被建的库

退出码：0 = 全部 nt 就位（或 --check 下不缺）；1 = 有库建失败。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
CFG = os.path.join(CODES, "config")
KBS_FILE = os.path.join(CFG, "kbs.json")

sys.path.insert(0, CODES)

# 需要语义关系（显式 rels）的库走各自 demo 的建模入口，不能用通用推断：
#   valve 的 "P004 用了哪些原料" 依赖 produces / usesRawMaterial 边
#   food  的溯源依赖 belongsToBatch / usesRawMaterial / produces 边
SEMANTIC_BUILDERS = {
    "valve": "valve_demo.build()",
    "food": "food_demo._build_kb()",
}


def load_kbs():
    with open(KBS_FILE, encoding="utf-8") as f:
        return json.load(f)["kbs"]


def _nt_path(kb, cfg):
    return os.path.join(CODES, cfg["nt"])


def build_semantic(kb, cfg):
    """走 demo 的建库入口（含显式语义关系）。"""
    if kb == "valve":
        import valve_demo
        valve_demo.build()
    elif kb == "food":
        import food_demo
        food_demo._build_kb()
    return _nt_path(kb, cfg)


def build_generic(kb, cfg):
    """通用路径：读数据目录 -> 推断 schema -> 写 nt。不写词典、不改注册表。"""
    import schema_ontology as so

    data_dir = os.path.join(CODES, cfg["data_dir"])
    data = so.load_all(data_dir)
    if not data:
        raise RuntimeError("数据目录读不到表: %s" % data_dir)
    schema = so.suggest_schema(data, use_llm=False)
    nt = _nt_path(kb, cfg)
    os.makedirs(os.path.dirname(nt), exist_ok=True)
    so.to_nt(data, schema, outpath=nt)
    if not os.path.exists(nt):
        raise RuntimeError("to_nt 未生成 %s" % nt)
    return nt


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    check = "--check" in sys.argv
    kbs = load_kbs()
    targets = args or sorted(kbs)

    unknown = [k for k in targets if k not in kbs]
    if unknown:
        print("未知库: %s（可选: %s）" % (unknown, ", ".join(sorted(kbs))))
        return 1
    if "--list" in sys.argv:
        print("\n".join(targets))
        return 0

    if check:
        missing = [k for k in targets if not os.path.exists(_nt_path(k, kbs[k]))]
        if missing:
            print("缺 nt（%d 个）: %s" % (len(missing), ", ".join(missing)))
            return 1
        print("nt 齐全（%d 个库）" % len(targets))
        return 0

    os.makedirs(os.path.join(CODES, "output"), exist_ok=True)
    bad = []
    for kb in targets:
        cfg = kbs[kb]
        try:
            if kb in SEMANTIC_BUILDERS:
                path = build_semantic(kb, cfg)
                tag = "语义关系"
            else:
                path = build_generic(kb, cfg)
                tag = "通用推断"
            size = os.path.getsize(path)
            print("  [ok]   %-14s %-8s %s (%d 字节)" % (kb, tag, cfg["nt"], size))
        except Exception as e:
            bad.append(kb)
            print("  [FAIL] %-14s %s: %s" % (kb, type(e).__name__, e))

    print("-" * 66)
    if bad:
        print("建库失败 %d 个: %s" % (len(bad), bad))
        return 1
    print("预建完成（%d 个库）" % len(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
