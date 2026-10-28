"""safe_log：静默吞异常的统一出口。

原则：请求路径上不允许 `except Exception: pass`——吞掉也要留一行日志，
否则线上只能看到"没反应"。真正的 best-effort（如 PRAGMA busy_timeout、
回滚前的重试）在捕获块上方标注 `# best-effort`，冻结门会放行。
"""
import sys
import traceback

_SEEN = set()


def swallow(where, exc, limit=200):
    """记录一次被吞掉的异常：同一位置只打前 limit 次，避免刷屏。"""
    key = where
    _SEEN[key] = _SEEN.get(key, 0) + 1
    if _SEEN[key] <= limit:
        sys.stderr.write("[swallowed] %s: %s: %s\n" % (where, type(exc).__name__, exc))
        if _SEEN[key] == limit:
            sys.stderr.write("[swallowed] %s: 后续同类异常不再打印\n" % where)
    return exc
