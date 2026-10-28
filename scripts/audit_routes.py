# -*- coding: utf-8 -*-
"""断链盘点：后端路由 ↔ 前端调用 ↔ 测试覆盖，输出三分类清单。

分类：
  A 前端已调用（正常）
  B 仅测试/脚本调用（运维与回归用，应进 allowlist 并在文档写明）
  C 无任何调用方（真断链，需删或补入口）
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
WEB = os.path.join(ROOT, "web", "src")


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def backend_routes():
    routes = {}
    for dp, dn, fn in os.walk(CODES):
        dn[:] = [d for d in dn if d not in {"__pycache__", "node_modules", "output", "data_demo_power"}]
        for f in fn:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            t = read(path)
            for m in re.finditer(r'@(?:app|router|api)\.(get|post|put|delete)\(\s*["\']([^"\']+)', t):
                routes.setdefault(m.group(2), set()).add(os.path.relpath(path, ROOT).replace("\\", "/"))
    return routes


def referenced(paths):
    """在指定目录里找 /api/... 字符串；返回 {route: [files]}"""
    hits = {}
    for base in paths:
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in {"node_modules", "__pycache__", "dist"}]
            for f in fn:
                if not f.endswith((".py", ".js", ".svelte", ".sh", ".bat", ".md")):
                    continue
                p = os.path.join(dp, f)
                t = read(p)
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-/{}.$:]*)', t):
                    hits.setdefault(m.group(1).rstrip("/"), set()).add(rel)
                # 也认 f"{BASE}/api/xxx" 形式
                for m in re.finditer(r'/api/[A-Za-z0-9_\-/]+', t):
                    hits.setdefault(m.group(0).rstrip("/"), set()).add(rel)
    return hits


def norm(route):
    return re.sub(r"\{[^}]*\}", "{}", route).rstrip("/")


def main():
    be = backend_routes()
    fe = referenced([WEB])
    test = referenced([os.path.join(ROOT, "tests"), os.path.join(CODES, "tests"),
                       os.path.join(ROOT, "docs")])

    A, B, C = [], [], []
    for route in sorted(be):
        n = norm(route)
        if n in fe or route in fe:
            A.append(route)
        elif n in test or route in test:
            B.append(route)
        else:
            C.append(route)

    print("后端路由 %d 条" % len(be))
    print("  A 前端已调用        %d" % len(A))
    print("  B 仅测试/脚本/文档   %d" % len(B))
    print("  C 无任何调用方       %d" % len(C))

    print("\nC 类（真断链）：")
    for r in C:
        print("  %-46s 定义于 %s" % (r, ", ".join(sorted(be[r]))[:60]))

    print("\nB 类（运维/回归用，需 allowlist）：")
    for r in B:
        print("  %-46s %s" % (r, ", ".join(sorted(test[r] if r in test else test[norm(r)]))[:60]))

    out = os.path.join(ROOT, ".dispatch", "route_audit.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(json.dumps(
        {"A": A, "B": B, "C": C, "defs": {k: sorted(v) for k, v in be.items()}},
        ensure_ascii=False, indent=2))
    print("\n明细:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
