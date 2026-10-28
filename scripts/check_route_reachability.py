# -*- coding: utf-8 -*-
"""断链门：后端每条 /api 路由，必须能在前端、测试、文档或运维 allowlist 里找到调用方。

判据：
  · 前端（web/src）里出现的路径优先；
  · 其次 tests/ 与 docs/（运维手册、验收脚本也算合法调用方）；
  · 都没有时，必须出现在 scripts/route_allowlist.txt 里并写理由；
  · 不在 allowlist 的算新断链 → FAIL。
非 /api 路径（首页、/health、/metrics、/admin）是页面与探活，不参与判定。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "codes")
ALLOW = os.path.join(ROOT, "scripts", "route_allowlist.txt")


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def backend_routes():
    routes = {}
    for dp, dn, fn in os.walk(CODES):
        dn[:] = [d for d in dn if d not in {"__pycache__", "node_modules", "output"}]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            t = read(p)
            for m in re.finditer(r'@(?:app|router|api)\.(get|post|put|delete)\(\s*["\'](/api/[^"\']+)', t):
                routes.setdefault(m.group(2).rstrip("/"), set()).add(
                    os.path.relpath(p, ROOT).replace("\\", "/"))
    return routes


def collected(targets):
    hits = set()
    for base in targets:
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in {"node_modules", "__pycache__", "dist"}]
            for f in fn:
                if not f.endswith((".py", ".js", ".svelte", ".md", ".sh", ".bat")):
                    continue
                t = read(os.path.join(dp, f))
                for m in re.finditer(r"/api/[A-Za-z0-9_\-/]+", t):
                    hits.add(m.group(0).rstrip("/"))
    return hits


def norm(r):
    return re.sub(r"\{[^}]*\}", "{}", r).rstrip("/")


def load_allow():
    if not os.path.isfile(ALLOW):
        return {}
    out = {}
    for ln in read(ALLOW).splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(None, 1)
        out[parts[0]] = parts[1] if len(parts) > 1 else ""
    return out


def main():
    routes = backend_routes()
    fe = {norm(x) for x in collected([os.path.join(ROOT, "web", "src")])}
    other = {norm(x) for x in collected([os.path.join(ROOT, "tests"), os.path.join(CODES, "tests"),
                                         os.path.join(ROOT, "docs"), os.path.join(ROOT, "scripts")])}
    allow = {norm(k): v for k, v in load_allow().items()}

    orphan = []
    for r in sorted(routes):
        n = norm(r)
        if n in fe or n in other or n in allow:
            continue
        orphan.append(r)

    print("后端 /api 路由 %d 条：前端可达 %d、测试/文档可达 %d、allowlist %d"
          % (len(routes), len([r for r in routes if norm(r) in fe]),
             len([r for r in routes if norm(r) in other]), len(allow)))
    if orphan:
        print("\n[FAIL] 新断链 %d 条（既无前端入口，也未登记 allowlist）：" % len(orphan))
        for r in orphan:
            print("  %s" % r)
        print("\n处理方式：补前端入口，或在 scripts/route_allowlist.txt 写一行「路径 理由」")
        print("RESULT: FAIL")
        return 1

    print("无新增断链")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
