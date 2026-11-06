"""操作留痕（七维之一）：谁提交了什么要留得下来。

分两层验证：

1. 协议层：`core.audit.record()` 有没有把条目交给写入端、写入端抛异常会不会影响业务；
2. 端到端：提交一次生图，库里有没有对应的 `audit_events` 行。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from core import audit

API = "/api/v1"


@pytest.fixture(autouse=True)
def _restore_sink() -> None:
    """每个用例结束后把写入端还原成 app 启动时注册的数据库实现。"""
    from db.audit_sink import write

    yield
    audit.set_audit_sink(write)


def test_record_hands_entry_to_sink() -> None:
    captured: list[audit.AuditEntry] = []

    async def sink(entry: audit.AuditEntry) -> None:
        captured.append(entry)

    audit.set_audit_sink(sink)
    asyncio.run(
        audit.record(
            "image.generate",
            actor_id="u_001",
            target_type="image_job",
            target_id="abc",
            detail={"model": "m", "count": 2},
        )
    )

    assert len(captured) == 1
    entry = captured[0]
    assert entry.action == "image.generate"
    assert entry.actor_id == "u_001"
    assert entry.detail == {"model": "m", "count": 2}


def test_sink_failure_never_breaks_business() -> None:
    """留痕写失败不能把用户的重试按钮变成 500 —— 这是这个扩展点的底线。"""

    async def broken(entry: audit.AuditEntry) -> None:
        raise RuntimeError("审计库挂了")

    audit.set_audit_sink(broken)
    asyncio.run(audit.record("image.generate", actor_id="u_001"))  # 不抛


def test_without_sink_record_is_a_noop() -> None:
    audit.reset_audit_sink()
    asyncio.run(audit.record("image.generate", actor_id="u_001"))


def test_generating_an_image_leaves_an_audit_row(client: TestClient) -> None:
    login = client.post(
        f"{API}/identity/login", json={"username": "admin", "password": "admin@12345"}
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}

    created = client.post(
        f"{API}/generations/image",
        headers=headers,
        json={"prompt": "审计验证：一杯咖啡的特写", "size": "1024x1024", "count": 1},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["id"]

    async def fetch() -> list[object]:
        from sqlalchemy import select

        from db.audit import AuditEvent
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            return list(
                (
                    await session.scalars(
                        select(AuditEvent).where(
                            AuditEvent.action == "image.generate",
                            AuditEvent.target_id == job_id,
                        )
                    )
                ).all()
            )

    rows = asyncio.run(fetch())
    assert len(rows) == 1, "提交生图必须留一条痕，否则出了问题只能翻日志"
    row = rows[0]
    assert row.actor_id == "u_001"
    assert row.target_type == "image_job"
    assert "\"model\"" in row.detail
