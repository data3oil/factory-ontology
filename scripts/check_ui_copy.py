# -*- coding: utf-8 -*-
"""对外文案与品牌一致性门：界面不出现 emoji，关键行必须在。

为什么要有这道门：emoji 出现两处就够毁掉"这是企业软件"的判断；
品牌行、许可、版权是三油科技对外交付物的固定件，漏一处就是合规缺口。

判据：
1. web/src 与 api_server.py 的界面字符串里不允许 emoji（箭头 ✓ ✗ 等符号不算）；
2. 登录页必须含品牌行「北京三油科技」+「Apache-2.0」+ 版权年；
3. 8000 落地页必须含「进入知识问答界面」入口与 /docs 链接。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# emoji 区段（不含 ✓ ✗ → ← · 等常用符号）
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF]"
)

SRC_FILES = ["web/src/App.svelte", "codes/api_server.py"]


def read(p):
    return io.open(os.path.join(ROOT, p), encoding="utf-8", errors="replace").read()


def main():
    fails = []

    # 1. emoji 清扫
    for rel in SRC_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        t = read(rel)
        lines = t.splitlines()
        for m in EMOJI.finditer(t):
            line = t[: m.start()].count("\n") + 1
            window = " ".join(lines[max(0, line - 2):line])   # 本行 + 上一行
            if "ui-copy-allow" in window:
                continue   # 客户自选标识（如企业 logo 预设）不算界面装饰
            fails.append("%s:%d 出现 emoji %r" % (rel, line, m.group(0)))

    # 2. 登录页品牌与许可行
    app = read("web/src/App.svelte")
    for must in ("北京三油科技", "Apache-2.0", "© 2026"):
        if must not in app:
            fails.append("登录页缺少「%s」" % must)
    if "login-legal" not in app:
        fails.append("登录页缺少版权行样式 .login-legal")

    # 3. 8000 落地页入口
    api = read("codes/api_server.py")
    for must in ("进入知识问答界面", "/docs", "进入知识问答界面"):
        if must not in api:
            fails.append("api_server 落地页缺少「%s」" % must)

    if fails:
        print("对外文案检查：%d 处问题" % len(fails))
        for f in fails[:15]:
            print("  -", f)
        print("RESULT: FAIL")
        return 1

    print("对外文案检查：无 emoji；品牌行、许可、版权、入口齐备")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
