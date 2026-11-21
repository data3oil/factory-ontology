# -*- coding: utf-8 -*-
"""verify_evolve_pending.py — 人在环底线：不在待确认区的名字不许写词典

背景（2026-11-22 审查）：confirm() 原来只判 name 非空，随便一个名字都会
并入词典、版本 +1、写快照，人在环退化成"随便加词"。

本门只做**否定路径**，不碰真实候选：拿一个绝不存在的名字去确认，
断言被拒、词典字节不变、版本不变。
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "codes"))
os.chdir(os.path.join(ROOT, "codes"))
import ontology_evolve as ev  # noqa: E402

KB = "valve"
PROBE = "__probe_not_in_pending__"


def _digest(path):
    if not os.path.exists(path):
        return ""
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    lex_path = ev._lexicon_path(KB)
    if not os.path.exists(lex_path):
        print("SKIP: 缺 %s" % lex_path)
        return 0
    before_hash = _digest(lex_path)
    before_ver = ev.alias_version(KB)

    ok, msg = ev.confirm(PROBE, kb=KB, actor="probe")
    after_hash = _digest(lex_path)
    after_ver = ev.alias_version(KB)

    checks = [
        ("不在待确认区被拒", ok is False),
        ("拒绝理由可读", "候选不存在" in str(msg)),
        ("词典字节未变", before_hash == after_hash),
        ("版本未变", before_ver == after_ver),
    ]
    bad = [n for n, passed in checks if not passed]
    for n, passed in checks:
        print(("  [PASS] " if passed else "  [FAIL] ") + n)
    print("  实测: ok=%s msg=%s 版本 %s→%s" % (ok, msg, before_ver, after_ver))
    print("自检结果: %s（%d/%d）" % ("PASS" if not bad else "FAIL", len(checks) - len(bad), len(checks)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
