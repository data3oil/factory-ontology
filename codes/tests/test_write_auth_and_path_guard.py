#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1/S2 安全回归门（2026-11-21，从开源快照线移植到主线时固化）。

- S1 写端点鉴权：知识库写状态端点必须 admin 权限，read key 不得写。
  （快照线 2026-11-20 发现 read key 可写 7 个写端点；主线 2026-11-21 移植修复）
- S2 路径穿越：admin_upload 的 table 参数不得含路径分隔符，realpath 不得越出 data 目录。

口径：read key 用例只断言鉴权拒绝（401/403），不落盘；路径穿越用例在校验阶段抛 400，
      同样不落盘；admin key + 合法 table 不在这里真跑（会污染 data，由 e2e 覆盖）。

运行: cd codes && python -m pytest tests/test_write_auth_and_path_guard.py -q
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # codes/
sys.path.insert(0, ROOT)

os.environ.setdefault("FOOD_ADMIN_KEY", "test-admin-key")
os.environ.setdefault("FOOD_READ_KEY", "test-read-key")
os.environ.setdefault("FOOD_TOKEN_SECRET", "test-token-secret-0123456789abcdef")

import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-API-Key": os.environ["FOOD_ADMIN_KEY"]}
READ = {"X-API-Key": os.environ["FOOD_READ_KEY"]}

# 写状态端点：路径 + 无副作用的探测 body（鉴权拒绝发生在业务逻辑之前）
WRITE_ENDPOINTS = [
    ("/api/assets/snapshot", {"kb": "food"}),
    ("/api/assets/rollback", {"kb": "food"}),
    ("/api/ontology/build", {"kb": "food"}),
    ("/api/ontology/confirm", {"kb": "food", "schema": {"entities": []}}),
    ("/api/ontology/self-onboard", {}),
    ("/api/kbs/food/examples", {}),
    ("/api/kb/active", {"kb": "food"}),
    ("/api/kb/food/lexicon/import", {}),
]

READ_ENDPOINTS = [
    ("/api/ask", {"question": "乳制品的数量", "kb": "food"}),
    ("/api/ontology/suggest", {}),
]


@pytest.mark.parametrize("path,body", WRITE_ENDPOINTS)
def test_read_key_cannot_write(path, body):
    """S1: read key 调写状态端点必须被拒（401/403），不得因 fail-open 放行。"""
    import api_server as api
    client = TestClient(api.app)
    resp = client.post(path, headers=READ, json=body)
    assert resp.status_code in (401, 403), (
        f"S1 回归: read key 写 {path} 应被拒, 实得 {resp.status_code}"
    )


def test_read_key_still_reads():
    """护栏: S1 修复不得牵连读端点 —— read key 问答案例仍 200。"""
    import api_server as api
    client = TestClient(api.app)
    resp = client.post("/api/ask", headers=READ,
                       json={"question": "乳制品的数量", "kb": "food"})
    assert resp.status_code == 200, (
        f"S1 护栏: read key 问答应 200, 实得 {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize("evil", ["../../evil", "../x", "a/b", "food_../x"])
def test_admin_upload_rejects_path_traversal(evil):
    """S2: table 含路径分隔符/穿越序列必须 400，且在落盘前被拒。"""
    import api_server as api
    client = TestClient(api.app)
    resp = client.post(
        "/api/admin/upload?table=" + evil,
        headers=ADMIN,
        files={"file": ("t.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert resp.status_code == 400, (
        f"S2 回归: table={evil!r} 应 400, 实得 {resp.status_code}"
    )
