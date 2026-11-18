#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_holdout.py — 留出域泛化实验：跨库积累的词典到底能不能迁移到没见过的行业。

要回答的问题
------------
这个项目的卖点是"换行业也能当场建本"。但词表是怎么来的？大多是从别的库攒出来的。
于是必须回答：**在没参与积累的库上，攒出来的词表还有用吗？**

实验设计（固定口径，可复现）
----------------------------
  留出域（holdout）：指定的若干库，实验时视作"没见过的新行业"。
  三档词典，同一份 nt、同一套题，只换词典：
    self    只用本库词典            —— 下限：不借任何外力
    public  本库 + 公共工业词典      —— 现状：`load_dict` 的默认行为
    pooled  本库 + 其他库攒的词表    —— 待验：跨库迁移是否有效
  题库由 `eval_qa.gen_kb_questions` 从 CSV 确定性生成（同数据同题，不看引擎输出）。

读法
----
  pooled − self   跨库积累的净增益（负值 = 负迁移，别硬搬）
  public − self   公共工业词典的净增益
  pooled − public 跨库积累相对公共词典的增量（>0 说明"攒库"比"买词典"更对路）

口径边界（必须说清）
--------------------
  · 只换词典，不动建本（nt 用的是已导出结果）。所以测的是**词表迁移**，不是建本迁移。
  · pooled 的来源库全量记录在报告里，便于判断有没有同行业泄漏。
  · 题库只用 `eval_qa.KB_META` 覆盖的库；没覆盖的库跳过并在报告里注明。

用法
----
  python scripts/eval_holdout.py                          # 默认 4 个留出库
  python scripts/eval_holdout.py --holdout valve,food     # 指定留出库
  python scripts/eval_holdout.py --json scripts/holdout_report.json
  python scripts/eval_holdout.py --min-donor 2            # pooled 只用出现在 ≥2 个来源库的词
"""
import argparse
import glob
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
CODES = os.path.join(ROOT, "codes")
for p in (SCRIPTS, CODES):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_HOLDOUT = ["valve", "food", "chem", "auto_parts"]


def _lex_path(kb):
    return os.path.join(CODES, "config", "lexicon_%s.json" % kb)


def _load_lex(kb):
    p = _lex_path(kb)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _donor_counts(kbs, exclude):
    """统计其他库的中文词条出现次数：{(映射字段, 中文, 英文): 出现库数}。

    只搬"属性名 / 状态值 / 类型值"三类映射 —— 这三类是跨行业可复用的词汇；
    实体名和实体到表的映射不能搬（每个库的表名不同），搬了必然错。
    """
    cnt = {}
    for kb in kbs:
        if kb in exclude:
            continue
        lex = _load_lex(kb)
        if not lex:
            continue
        for field in ("attr_cn2en", "status_cn2en", "type_cn2en"):
            for cn, en in (lex.get(field) or {}).items():
                if not cn or not en:
                    continue
                key = (field, str(cn), str(en))
                cnt[key] = cnt.get(key, 0) + 1
    return cnt


def _write_temp(kb, mode, doc):
    """写一份临时词典（不覆盖原文件），返回路径。"""
    fd, path = tempfile.mkstemp(prefix="lex_%s_%s_" % (kb, mode), suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False)
    return path


def build_lex(kb, mode, donors, min_donor=1):
    """构造该档词典，返回 (临时文件路径 | None, 说明)。

    四档的设计意图（这是本实验的核心）：
      zero   空词典 —— **新行业冷启动的真实下限**：没有任何人工词表，只剩数据本身
      public 只用公共工业词典 —— "买一份通用词典"这条路的收益
      pooled 只用其他库攒的词表 —— "从已有库迁移"这条路的收益
      self   本库完整词典 —— 上限参照（不参与泛化结论，只用来看差距）

    注意 pooled **不含本库词表**：含了就不是"迁移"而是"本库+外挂"，差异会被本库词表淹没。
    """
    if mode == "self":
        # 与生产口径一致：本库词典 + 公共工业词典（load_dict 默认行为）
        return ("__prod__", "本库词典 + 公共层（生产默认口径）")
    if mode == "zero":
        return _write_temp(kb, mode, {"attr_cn2en": {}, "status_cn2en": {},
                                      "type_cn2en": {}, "entity_cn2en": {}}), "空词典"
    if mode == "public":
        return None, "公共工业词典（load_dict 默认合并）"
    # pooled：只搬其他库的词，按出现库数过滤
    doc = {"attr_cn2en": {}, "status_cn2en": {}, "type_cn2en": {}, "entity_cn2en": {}}
    for (field, cn, en), n in donors.items():
        if n < min_donor:
            continue
        if isinstance(doc.get(field), dict) and cn not in doc[field]:
            doc[field][cn] = en
    return _write_temp(kb, mode, doc), "其他库词表（%d 条，min_donor=%d）" % (
        sum(len(v) for v in doc.values() if isinstance(v, dict)), min_donor)


def run_mode(kb, mode, lex_path, questions, suite="auto"):
    """用指定词典跑一遍题库，返回统计。纯内存，不起 HTTP。"""
    import ontology_qa_v3 as v3
    import eval_qa as EQ
    cfg = EQ.load_kbs()[kb]
    nt = os.path.join(CODES, cfg["nt"])
    if mode == "public":
        # 只保留公共工业词典这一层：以空词典为底，让 load_dict 去合并公共层
        fd, tmp = tempfile.mkstemp(prefix="lex_%s_public_" % kb, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"attr_cn2en": {}, "status_cn2en": {},
                       "type_cn2en": {}, "entity_cn2en": {}}, f, ensure_ascii=False)
        try:
            # 显式给行业，才能把"行业层"也纳入；否则只合并基础层，会低估公共词典
            try:
                from industrial_dict_loader import industry_for_kb
                _ind = industry_for_kb(kb)
            except Exception:
                _ind = None
            D = v3.load_dict(tmp, industry=_ind)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
    elif lex_path == "__prod__":
        D = v3.load_dict(os.path.join(CODES, "config", cfg["lexicon"]))  # 生产口径
    else:
        with open(lex_path, encoding="utf-8") as f:
            D = json.load(f)               # 完全隔离：不合并公共层（测纯迁移）
    data = v3.build_data(v3.parse_nt(nt), D)

    per = {k: 0 for k in ("total", "answerable", "hit", "miss", "wrong",
                          "noans", "refuse_ok", "fabricate", "empty_zero")}
    for q in questions:
        per["total"] += 1
        try:
            ans = str(v3.answer(q["question"], data, D))
        except Exception as e:
            ans = "[引擎异常] %s" % e
        if suite == "cq":
            import verify_cq_baseline as VC
            bucket, _detail = VC.judge_cq(q, ans)   # ok/miss/wrong/fabricate
            if (q.get("expect") or {}).get("mode") == "refuse":
                per["noans"] += 1
                if bucket == "ok":
                    per["refuse_ok"] += 1
                else:
                    per["fabricate"] += 1           # 无答案题给了具体答案 = 编造
            else:
                per["answerable"] += 1
                if bucket == "ok":
                    per["hit"] += 1
                elif bucket == "wrong":
                    per["wrong"] += 1
                else:
                    per["miss"] += 1
            continue
        bucket, _ok = EQ.judge(q, ans)
        if q.get("answerable", True):
            per["answerable"] += 1
            per[bucket if bucket in ("hit", "miss", "wrong") else "miss"] += 1
        else:
            per["noans"] += 1
            per[bucket if bucket in ("refuse_ok", "fabricate", "empty_zero") else "fabricate"] += 1
    per.update(EQ.fmt_rates(per))
    return per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", default=",".join(DEFAULT_HOLDOUT))
    ap.add_argument("--json", default=None)
    ap.add_argument("--min-donor", type=int, default=1,
                    help="pooled 只接纳出现在 >=N 个来源库的词（默认 1）")
    ap.add_argument("--suite", choices=("auto", "cq"), default="cq",
                    help="题库：cq=业务口径（默认，区分度高）；auto=CSV 自动生成")
    a = ap.parse_args()

    holdouts = [x.strip() for x in a.holdout.split(",") if x.strip()]
    import eval_qa as EQ
    all_kbs = sorted(EQ.load_kbs().keys())
    meta_kbs = set(EQ.KB_META.keys())

    report = {"holdouts": [], "donors": sorted(set(all_kbs) - set(holdouts)),
              "min_donor": a.min_donor, "skipped": [], "modes": {}}
    donors = _donor_counts(all_kbs, set(holdouts))

    for kb in holdouts:
        if a.suite != "cq" and kb not in meta_kbs:
            report["skipped"].append({"kb": kb, "reason": "KB_META 未覆盖，无法生成题库"})
            print("[skip] %s：eval_qa.KB_META 未覆盖，无法生成确定性题库" % kb)
            continue
        if a.suite == "cq":
            qp = os.path.join(SCRIPTS, "cq", "%s.json" % kb)
            if not os.path.exists(qp):
                report["skipped"].append({"kb": kb, "reason": "无 CQ 题库"})
                print("[skip] %s：无 scripts/cq/%s.json" % (kb, kb))
                continue
            with open(qp, encoding="utf-8") as f:
                questions = json.load(f)["questions"]
            print("\n[%s] CQ 题库 %d 题（业务口径，人工写）" % (kb, len(questions)))
        else:
            tables = EQ.read_tables(os.path.join(CODES, "data_%s" % kb))
            questions = EQ.gen_kb_questions(kb, EQ.KB_META[kb], tables)
            print("\n[%s] 题库 %d 题（由 CSV 确定性生成）" % (kb, len(questions)))

        row = {"kb": kb, "questions": len(questions)}
        paths = []
        for mode in ("zero", "public", "pooled", "self"):
            lp, note = build_lex(kb, mode, donors, a.min_donor)
            if lp and lp != "__prod__":
                paths.append(lp)
            per = run_mode(kb, mode, lp, questions, a.suite)
            row[mode] = per
            print("  %-7s 命中 %3d/%3d = %.1f%%  误答 %d  未命中 %d  编造 %d   [%s]"
                  % (mode, per["hit"], per["answerable"], per["hit_rate"] * 100,
                     per["wrong"], per["miss"], per["fabricate"], note))
        for lp in paths:
            try:
                os.unlink(lp)
            except Exception:
                pass

        def gain(x, y):
            return round(row[x]["hit_rate"] - row[y]["hit_rate"], 4)
        # 增益都以"冷启动下限 zero"为基准：新行业没有任何人工词表时能答多少，才是真问题
        row["gain_pooled"] = gain("pooled", "zero")
        row["gain_public"] = gain("public", "zero")
        row["gain_pooled_vs_public"] = gain("pooled", "public")
        row["headroom"] = round(1 - row["self"]["hit_rate"], 4)
        print("  相对冷启动：跨库 %+0.1f%% / 公共 %+0.1f%% / 跨库比公共 %+0.1f%%（上限差距 %.1f%%）"
              % (row["gain_pooled"] * 100, row["gain_public"] * 100,
                 row["gain_pooled_vs_public"] * 100, row["headroom"] * 100))
        report["holdouts"].append(row)

    # 汇总
    valid = report["holdouts"]
    if valid:
        for m in ("zero", "public", "pooled", "self"):
            h = sum(r[m]["hit"] for r in valid)
            n = sum(r[m]["answerable"] for r in valid) or 1
            report["modes"][m] = {"hit": h, "answerable": n, "hit_rate": round(h / n, 4)}
        print("\n合计（%d 个留出库）" % len(valid))
        for m in ("zero", "public", "pooled", "self"):
            v = report["modes"][m]
            print("  %-7s 命中 %3d/%3d = %.1f%%" % (m, v["hit"], v["answerable"],
                                                  v["hit_rate"] * 100))
        md = report["modes"]
        print("  pooled − zero = %+.1f 个百分点（跨库词表能不能救冷启动）"
              % ((md["pooled"]["hit_rate"] - md["zero"]["hit_rate"]) * 100))
        print("  public − zero = %+.1f 个百分点（公共工业词典能不能救冷启动）"
              % ((md["public"]["hit_rate"] - md["zero"]["hit_rate"]) * 100))
        print("  self − pooled = %+.1f 个百分点（离人工词典还差多远）"
              % ((md["self"]["hit_rate"] - md["pooled"]["hit_rate"]) * 100))

    print("\n口径：只换词典不重建 nt，测的是词表迁移；pooled 来源 %d 库，min_donor=%d"
          % (len(report["donors"]), a.min_donor))
    if report["skipped"]:
        print("跳过 %d 个库（见报告 skipped）" % len(report["skipped"]))

    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("报告 → %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
