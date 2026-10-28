# -*- coding: utf-8 -*-
"""静默吞异常冻结门：`except Exception: pass` 数量只降不升。

两道判据：
1. 请求路径上的捕获必须留日志（用 safe_log.swallow），不许静默 pass；
2. 真正的 best-effort（PRAGMA busy_timeout、回滚重试等）必须在捕获块上方写 `# best-effort` 注明理由。

当前剩余数量作为上限冻结；新增一处就红。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = 33          # 2026-10 去异常首轮：由 36 降到 33 后冻结，只降不升
PAT = re.compile(r"except\s+Exception\s*:\s*\n\s*pass")


def main():
    hits, marked = [], 0
    for dp, dn, fn in os.walk(os.path.join(ROOT, "codes")):
        dn[:] = [d for d in dn if d not in {"__pycache__", "node_modules", "output"}]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            t = io.open(p, encoding="utf-8", errors="replace").read()
            for m in PAT.finditer(t):
                line = t[: m.start()].count("\n") + 1
                prev = t.splitlines()[line - 2].strip() if line >= 2 else ""
                if "best-effort" in prev:
                    marked += 1
                    continue
                hits.append("%s:%d" % (os.path.relpath(p, ROOT).replace("\\", "/"), line))

    print("静默吞异常：未标注 %d 处（上限 %d），已标注 best-effort %d 处" % (len(hits), CAP, marked))
    if hits:
        for h in hits[:12]:
            print("  ", h)
    if len(hits) > CAP:
        print("RESULT: FAIL（新增了静默吞异常，请改用 safe_log.swallow 或标注 best-effort）")
        return 1
    print("RESULT: PASS（只降不升）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
