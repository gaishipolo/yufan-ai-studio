"""成片取件：归档存储优先，工作区兜底。

两个分支都必须能走通，因为它们对应两种真实情况：

- `output_key` 有值：成片已经搬进归档存储。远端驱动下交给 307，字节不经过
  应用进程；本地驱动下 `download_url` 返回 None，退回文件直出。
- `output_key` 为空：重建前的老档案，或者上传失败的那些。文件还在工作区里，
  本地兜底让它们照样能播——但换台机器就打不开了，这是已知代价。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from db.session import get_session_factory
from infra.archive import get_archive_storage
from infra.archive.runs import relative_key, runs_root, save_pointer
from modules.template.models import ReplacementRun
from modules.template.workspace import ensure_layout, write_state

API = "/api/v1"
PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"frame" * 64


def _me_id(client: TestClient) -> str:
    resp = client.get(f"{API}/identity/me")
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


async def _add_run(run_id: str, owner: str, *, output_key: str | None) -> None:
    async with get_session_factory()() as session:
        session.add(
            ReplacementRun(
                id=run_id,
                kind="product",
                target="取件用例",
                owner_id=owner,
                fingerprint="output-check",
                status="success",
                output_key=output_key,
            )
        )
        await session.commit()


async def _drop_run(run_id: str) -> None:
    async with get_session_factory()() as session:
        row = await session.get(ReplacementRun, run_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


def _workspace(run_id: str) -> Path:
    path = runs_root() / f"product-{run_id}"
    ensure_layout(path)
    write_state(path, {"id": run_id, "status": "success"})
    save_pointer(get_archive_storage(), run_id, relative_key(path))
    return path


def test_output_served_from_archive_storage(client: TestClient) -> None:
    """`output_key` 有值时从归档存储取字节，不看工作区。"""
    owner = _me_id(client)
    run_id = uuid.uuid4().hex
    key = f"runs/{run_id}/outputs/generated.mp4"
    try:
        path = _workspace(run_id)
        # 故意不在工作区放成片：走了兜底分支就会 404，测试会立刻指出来。
        staging = path / "staging.bin"
        staging.write_bytes(PAYLOAD)
        get_archive_storage().copy(staging, key)
        staging.unlink()
        asyncio.run(_add_run(run_id, owner, output_key=key))

        response = client.get(f"{API}/templates/replacement-runs/{run_id}/output")
        assert response.status_code == 200, response.text
        assert response.content == PAYLOAD
    finally:
        asyncio.run(_drop_run(run_id))


def test_output_falls_back_to_workspace(client: TestClient) -> None:
    """`output_key` 为空时读工作区：老档案不能因为没搬过就播不了。"""
    owner = _me_id(client)
    run_id = uuid.uuid4().hex
    try:
        path = _workspace(run_id)
        (path / "outputs" / "generated.mp4").write_bytes(PAYLOAD)
        asyncio.run(_add_run(run_id, owner, output_key=None))

        response = client.get(f"{API}/templates/replacement-runs/{run_id}/output")
        assert response.status_code == 200, response.text
        assert response.content == PAYLOAD
    finally:
        asyncio.run(_drop_run(run_id))


def test_output_missing_is_404(client: TestClient) -> None:
    """既没登记归档、工作区也没文件：404，而不是 500 或空文件。"""
    owner = _me_id(client)
    run_id = uuid.uuid4().hex
    try:
        _workspace(run_id)
        asyncio.run(_add_run(run_id, owner, output_key=None))
        response = client.get(f"{API}/templates/replacement-runs/{run_id}/output")
        assert response.status_code == 404, response.text
    finally:
        asyncio.run(_drop_run(run_id))
