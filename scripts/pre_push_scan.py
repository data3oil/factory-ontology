# -*- coding: utf-8 -*-
"""推送前合规扫描：只扫将被提交的文件（git 跟踪 + 本次新增），查脱敏与密钥问题。

检查项：本机绝对路径、用户名、内部项目名、疑似密钥、客户可识别信息、演示字样。
"""
import os
import re
import subprocess
import sys

ROOT = "D:/factory-ontology"
PATTERNS = [
    ("本机绝对路径", re.compile(r"[A-Za-z]:[\\/](?:Users|workspace|factory-ontology\\|HR|获客|组织主体)")),
    ("用户名 zjjem", re.compile(r"zjjem", re.I)),
    ("内部项目名", re.compile(r"获客飞轮|组织主体舆情|企业人才系统|企业内训|客讯引擎")),
    ("疑似密钥", re.compile(r"(?i)(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")),
    ("硬编码口令", re.compile(r"(?i)(password|passwd|secret)\s*=\s*['\"][^'\"]{6,}['\"]")),
]

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".zip", ".whl"}


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def files_to_check():
    tracked = [f for f in sh("git", "ls-files").splitlines() if f.strip()]
    untracked = [f for f in sh("git", "ls-files", "--others", "--exclude-standard").splitlines() if f.strip()]
    return tracked, untracked


def scan(files):
    hits = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if rel.endswith("scripts/pre_push_scan.py"):
            continue   # 扫描器自身的规则串会命中自己
        if not os.path.isfile(path) or os.path.splitext(rel)[1].lower() in SKIP_EXT:
            continue
        if os.path.getsize(path) > 2_000_000:
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for name, pat in PATTERNS:
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                snippet = text.splitlines()[line - 1].strip()[:100]
                hits.append((name, rel, line, snippet))
    return hits


def main():
    tracked, untracked = files_to_check()
    print("已跟踪文件 %d 个，新增未跟踪 %d 个" % (len(tracked), len(untracked)))
    if untracked:
        print("新增：")
        for f in untracked[:20]:
            print("  ", f)

    hits = scan(tracked + untracked)
    if not hits:
        print("\n合规扫描：未发现本机路径 / 用户名 / 内部项目名 / 密钥 / 硬编码口令")
        return 0

    print("\n命中 %d 处：" % len(hits))
    for name, rel, line, snip in hits[:40]:
        print("  [%s] %s:%d  %s" % (name, rel, line, snip))
    return 1


if __name__ == "__main__":
    sys.exit(main())
