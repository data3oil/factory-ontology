#!/usr/bin/env python3
"""DSN 解析的跨平台回归（2026-11-18）。

起因：`parse_dsn` 曾用 `re.sub(r"^/+", "", db)` 把所有前导 / 去掉。
Windows 上 `/C:/x.db` 去掉正好对，POSIX 上 `/tmp/x.db` 被吃成相对路径
`tmp/x.db`，`db_loader` 于是一路报 "SQLite 库不存在"。
本机是 Windows，永远测不出来，直到 17 道门接进 CI 的 Linux runner 才暴露。

这里只做纯字符串断言（不碰文件系统），所以 Windows 上跑也能挡住同类回归。

运行: cd codes && python -m pytest tests/test_db_dsn.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)

import db_loader as DL  # noqa: E402


def _db(dsn):
    """parse_dsn 返回 (db_type, host, port, user, password, database)。"""
    return DL.parse_dsn(dsn)[5]


# ── SQLite：三种写法 ──────────────────────────────────────────────────────────
def test_sqlite_relative_path():
    # sqlite:/// 的第三个 / 是 URL 分隔符，不是路径的一部分
    assert _db("sqlite:///factory.db") == "factory.db"
    assert _db("sqlite:///tmp/x.db") == "tmp/x.db"


def test_sqlite_posix_absolute_path():
    # 多出来的那个 / 表示根目录，必须保留
    assert _db("sqlite:////tmp/x.db") == "/tmp/x.db"
    assert _db("sqlite:////var/lib/factory.db") == "/var/lib/factory.db"


def test_sqlite_windows_absolute_path():
    assert _db("sqlite:///C:/data/factory.db") == "C:/data/factory.db"
    # 多写一个 / 的写法（sqlite:////C:/x），归一化回 C:/x
    assert _db("sqlite:////C:/data/factory.db") == "C:/data/factory.db"


def test_absolute_path_stays_absolute():
    """这条是本次事故的靶心：POSIX 绝对路径不许被降级成相对路径。

    不判 os.path.isabs：在 Windows 上 '/tmp/x' 不算绝对路径，那会把
    "本机平台差异"混进"解析对不对"的断言里。只看字符串本身。
    """
    db = _db("sqlite:////tmp/verify_hardening_x/dialect_test.db")
    assert db == "/tmp/verify_hardening_x/dialect_test.db", "解析结果: %r" % db
    assert not db.startswith("tmp/"), "前导 / 被吃掉了: %r" % db


def test_sqlite_case_insensitive_scheme():
    assert _db("SQLite:////tmp/x.db") == "/tmp/x.db"


# ── 其它库型不受影响 ──────────────────────────────────────────────────────────
def test_mysql_dsn():
    kind, host, port, user, pwd, db = DL.parse_dsn("mysql://u:p@127.0.0.1:3306/factory")
    assert (kind, host, port, user, pwd, db) == ("mysql", "127.0.0.1", 3306, "u", "p", "factory")


def test_postgres_dsn_without_port():
    # parse_dsn 会把库型归一化（postgresql → postgres）
    kind, host, port, _u, _p, db = DL.parse_dsn("postgresql://u:p@db.local/factory")
    assert (kind, host, port, db) == ("postgres", "db.local", 0, "factory")


def test_unknown_dsn_raises():
    try:
        DL.parse_dsn("redis://localhost/0")
    except ValueError:
        return
    raise AssertionError("未知库型应抛 ValueError")
