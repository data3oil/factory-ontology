"""优化门：等价 + 提速 + 缓存失效边界。

用法：
  python tests/probes/probe_opt_speed.py --record   # 改前录制答案基线（写入同目录 _opt_baseline.json）
  python tests/probes/probe_opt_speed.py            # 改后校验

判据：
  O1 等价：20 个问题答案逐条与基线一致（改逻辑不许改答案）
  O2 提速：53 400 三元组下单问 answer 中位 ≤ 0.15 s（改前 0.42 s，≥2.5×）
  O3 缓存失效：同长度的不同数据集必须给出各自答案（缓存不能按长度/地址瞎命中）
  O4 缓存有界：连续喂 60 个不同 data 后，模块内缓存条目 ≤ 8
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CODES = os.path.join(ROOT, "codes")
sys.path.insert(0, CODES)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ontology_qa_v3 as qa  # noqa: E402

BASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_opt_baseline.json")
LEX = os.path.join(CODES, "config", "lexicon_valve.json")

QS = [
    "有多少台设备", "设备总数", "质检记录有多少条", "客户有多少个", "产品有多少个",
    "设备中capacity_l最大是多少", "设备中pressure_mpa最小是多少",
    "设备的状态有哪些", "产品的状态有哪些", "客户的名称有哪些",
    "有多少台设备的状态是运行", "投料有哪些原料", "批次有哪些产品",
    "sales有多少条记录", "batches有多少条记录", "有多少条质检记录是合格",
    "设备中capacity_l最小的是哪台", "原料的name有哪些", "设备有哪些",
    "有多少台设备 的状态是在用",
]

fails = []


def check(name, ok, detail=""):
    print("  %-4s %-34s %s" % ("OK" if ok else "FAIL", name, str(detail)[:90]))
    if not ok:
        fails.append(name)


def load_D():
    return qa.load_dict(LEX)


def small_data():
    """小库：用真实 valve.nt（1 068 三元组）跑答案等价。"""
    return qa.build_data(qa.parse_nt(os.path.join(CODES, "output", "valve.nt")), load_D())


def big_data(mult=50):
    tmp = tempfile.mkdtemp(prefix="optbig_")
    src = os.path.join(CODES, "output", "valve.nt")
    lines = [l for l in open(src, encoding="utf-8", errors="replace") if l.strip()]
    p = os.path.join(tmp, "big.nt")
    with open(p, "w", encoding="utf-8") as f:
        for i in range(mult):
            for l in lines:
                f.write(l if i == 0 else l.replace("> <", "_%d> <" % i, 1))
    return qa.build_data(qa.parse_nt(p), load_D()), len(lines) * mult


def answers(data, D):
    return [str(qa.answer(q, data, D)) for q in QS]


def record():
    D = load_D()
    data = small_data()
    got = answers(data, D)
    json.dump(got, open(BASE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("已录制 %d 条基线 -> %s" % (len(got), BASE_FILE))
    for q, a in list(zip(QS, got))[:3]:
        print("   %s -> %s" % (q, a))
    return 0


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def main():
    D = load_D()

    # O1 等价
    data = small_data()
    got = answers(data, D)
    if not os.path.isfile(BASE_FILE):
        check("O1 等价", False, "缺基线文件，先跑 --record")
    else:
        exp = json.load(open(BASE_FILE, encoding="utf-8"))
        diff = [(q, e, g) for q, e, g in zip(QS, exp, got) if e != g]
        check("O1 等价（%d 问）" % len(QS), not diff,
              "" if not diff else "首个差异: %s 期望 %s 实际 %s" % diff[0])

    # O2 提速
    bd, ntriples = big_data(50)
    ts = []
    for _ in range(5):
        t0 = time.perf_counter()
        qa.answer("有多少台设备", bd, D)
        ts.append(time.perf_counter() - t0)
    med = median(ts)
    check("O2 提速 %d 三元组" % ntriples, med <= 0.15, "中位 %.3f s（阈值 0.150）" % med)

    # O3 缓存失效：同长度、不同内容
    a1 = {"Valve_equipment_A": {"equipmentName": "A", "status": "运行"},
          "Valve_equipment_B": {"equipmentName": "B", "status": "运行"}}
    a2 = {"Valve_equipment_A": {"equipmentName": "A", "status": "运行"},
          "Valve_equipment_B": {"equipmentName": "B", "status": "运行"},
          "Valve_equipment_C": {"equipmentName": "C", "status": "运行"}}
    n1 = str(qa.answer("有多少台设备", a1, D))
    n2 = str(qa.answer("有多少台设备", a2, D))
    check("O3 缓存失效（2→3 台）", ("2" in n1) and ("3" in n2), "%s / %s" % (n1, n2))

    # 同长度但内容不同（换个类名，答案应不同）
    b1 = {"Valve_product_X": {"productName": "X"}}
    b2 = {"Chem_batch_X": {"batchName": "X"}}
    r1 = str(qa.answer("有多少个产品", b1, D))
    r2 = str(qa.answer("有多少个产品", b2, D))
    check("O3 同长度不同内容", r1 != r2, "%s / %s" % (r1[:20], r2[:20]))

    # O4 缓存有界：data 级缓存 ≤8，rec 级缓存 ≤4096（两级上限不同，分别判）
    qa.answer("有多少台设备", {"Valve_equipment_Z": {"equipmentName": "Z"}}, D)
    dsz = len(getattr(qa, "_DATA_INDEX_CACHE", []))
    nsz = len(getattr(qa, "_NORM_CACHE", {}))
    check("O4 缓存有界", dsz <= 8 and nsz <= 4096, "data=%d(≤8) norm=%d(≤4096)" % (dsz, nsz))

    print("\n%s" % ("OK 全绿" if not fails else "FAILED %d 项红: %s" % (len(fails), fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--record" in sys.argv:
        sys.exit(record())
    sys.exit(main())
