"""一把跑全部自检门 —— 收口用（也可给 CI 调用）。

用法：
    python scripts/run_all_gates.py            # 全部
    python scripts/run_all_gates.py --fast     # 跳过最慢的评测基线

退出码：0 = 全绿；1 = 有门不过（并打印是哪一个）。
纯标准库；不启常驻服务（各门自己起临时端口并收尾）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"

GATES = [
    ("KB 注册表与词典分层", ["scripts/verify_kb_registry.py"]),
    ("商用加固（鉴权/方言/审计链）", ["scripts/verify_hardening.py"]),
    ("编排上半截（流程/控制流/预设）", ["scripts/verify_flows.py"]),
    ("问答信封与未命中", ["scripts/verify_qa_envelope.py"]),
    ("实例名直查回归", ["scripts/verify_instance_name.py"]),
    ("模型建议层", ["scripts/verify_advisory.py"]),
    ("本体自演进（人在环）", ["scripts/verify_ontology_evolve.py"]),
    ("演进确认只认待确认区", ["scripts/verify_evolve_pending.py"]),
    ("本体 IRI 完整性（悬空/域值域/ASCII）", ["scripts/verify_iri_integrity.py"]),
    ("本体建模坑 lint（OOPS!）", ["scripts/verify_oops.py"]),
    ("SHACL 实例校验", ["scripts/verify_shacl.py"]),
    ("词典碎片与跨库对齐", ["scripts/verify_lexicon_align.py"]),
    ("本体检查（5 类核查·打分制）", ["codes/ontology_check.py"]),
    ("本体逻辑一致性（唯一/单值/链环/不相交）",
     ["codes/logic_check.py", "--schema", "codes/config/ontology_schema_valve.json",
      "--nt", "codes/output/valve.nt", "--axioms", "codes/config/axioms/valve.json"]),
    ("边界自检（甲方痕迹/依赖）", ["scripts/check_boundary.py"]),
    ("仓库单测", ["-m", "pytest", "codes/tests", "-q"]),
]
SLOW_GATES = [
    ("问答评测基线（回归门）", ["scripts/eval_qa.py"]),
    ("能力问题 CQ 基线（业务口径）", ["scripts/verify_cq_baseline.py"]),
    ("留出域泛化（实验，不阻断）", ["scripts/eval_holdout.py"]),
    ("OQuaRE 结构度量（打分，不阻断）", ["scripts/ontology_oquare.py"]),
]


def run(name, args):
    cmd = [PY] + args
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    tail = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]
    hits = [ln.strip() for ln in tail
            if any(k in ln for k in ("自检结果", "通过", "结论", "结果:", "passed", "failed", "违规"))]
    summary = hits[-1] if hits else (tail[-1] if tail else "(无输出)")
    return p.returncode, summary


def main():
    fast = "--fast" in sys.argv
    gates = GATES + ([] if fast else SLOW_GATES)
    bad = []
    print("=" * 74)
    print("全部门一览  仓库: %s" % ROOT)
    print("=" * 74)
    for name, args in gates:
        rc, summary = run(name, args)
        flag = "PASS" if rc == 0 else "FAIL"
        print("  [%s] %-28s rc=%d  %s" % (flag, name, rc, summary[:88]))
        if rc != 0:
            bad.append(name)
    print("-" * 74)
    if bad:
        print("不全绿：%d 个门未过 -> %s" % (len(bad), bad))
    else:
        print("全部通过（%d 个门）" % len(gates))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
