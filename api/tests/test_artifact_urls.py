"""产物地址：库里存 key，出参按当前驱动现签。

回归的是切驱动那一刻的失联：老实现把 `url_for()` 的产物落库，`/static` 只在
本地驱动挂载，切 S3 之后生图页整屏破图且改代码修不回来。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from infra.artifacts import artifact_url, artifact_urls, is_object_key


def test_object_key_vs_external_address() -> None:
    assert is_object_key("images/x.png")
    assert not is_object_key("/static/images/x.png")
    assert not is_object_key("https://vendor.example.com/x.png")
    assert not is_object_key("data:image/png;base64,AAA")
    assert not is_object_key("")
    assert not is_object_key(None)


def test_local_driver_signs_key_back_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地驱动没有签名语义：`presigned_download_url` 就是 `/static/<key>`。"""
    from infra.storage.local import LocalStorage

    monkeypatch.setattr("infra.get_storage", lambda: LocalStorage())

    assert asyncio.run(artifact_url("images/x.png")).endswith("/static/images/x.png")


def test_legacy_addresses_pass_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """老 `/static` 值与供应商直链不能二次拼装，否则会变成 `/static/https://…`。"""
    from infra.storage.local import LocalStorage

    monkeypatch.setattr("infra.get_storage", lambda: LocalStorage())

    urls = asyncio.run(
        artifact_urls(["/static/images/old.png", "https://vendor.example.com/signed?x=1", "", None])
    )

    assert urls == ["/static/images/old.png", "https://vendor.example.com/signed?x=1", "", ""]


def test_signature_failure_does_not_break_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一张图签不出来不该让整个列表 500：返回空串，前端显示占位图。"""
    from infra.storage.base import StorageError

    class Broken:
        async def presigned_download_url(self, key: str, *, expires_in: int = 0) -> str:
            raise StorageError("签名服务不可用")

    monkeypatch.setattr("infra.get_storage", lambda: Broken())

    assert asyncio.run(artifact_url("images/x.png")) == ""


def test_image_list_returns_address_not_key(client: TestClient, api: str) -> None:
    """端到端：落库是 key，出参必须是能直接喂给 `<img src>` 的地址。"""
    from db.session import get_session_factory
    from modules.image.models import ImageJob

    async def seed_row() -> str:
        async with get_session_factory()() as session:
            job = ImageJob(
                name="地址转换",
                prompt="雨夜霓虹",
                status="succeeded",
                model="Agnes",
                owner_id="u_001",
                output_url="images/artifact-url-e2e.png",
                cover_url="images/artifact-url-e2e-cover.png",
            )
            session.add(job)
            await session.commit()
            return job.id

    async def drop_row(job_id: str) -> None:
        from sqlalchemy import delete

        async with get_session_factory()() as session:
            await session.execute(delete(ImageJob).where(ImageJob.id == job_id))
            await session.commit()

    job_id = asyncio.run(seed_row())
    try:
        response = client.get(f"{api}/images", params={"pageSize": 100})
        assert response.status_code == 200, response.text
        row = next(item for item in response.json()["data"]["items"] if item["id"] == job_id)
        assert row["outputUrl"].endswith("/static/images/artifact-url-e2e.png")
        assert not row["outputUrl"].startswith("images/")
        assert row["coverUrl"].endswith("/static/images/artifact-url-e2e-cover.png")
    finally:
        asyncio.run(drop_row(job_id))
