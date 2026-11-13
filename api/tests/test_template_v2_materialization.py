"""资产库 v2 固定引用的生成物化：从对象存储搬进本次 run 的工作区。

这一层只回答「字节从哪来」，权限在 `authorization` / `readable_ids()` 已经判完。
三条判据都要钉住，否则失败方向都是**静默出片**：

1. 字节必须真的进工作区（`input-map.json` 的摘要与实算一致，执行器才肯上传）；
2. 宽高要带过去（`media-inputs.json` 少了它，供应商那头把图当未知尺寸）；
3. 运行输入行必须写上 `v2`（写成默认 `v1`，执行前的复核会去旧表找这个 id，
   报「输入已被删除」——一条本来能跑的任务被自己的标记挡死）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator

import pytest
from tests_template_helpers import (
    ADMIN,
    add_pack,
    drop_pack,
    login,
    png_bytes,
    upload,
    video_bytes,
)
from video_template_support import remove as remove_template

from core.exceptions import BadRequest
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetRef,
    LibraryAssetState,
    LibraryVisibility,
)
from db.session import get_session_factory
from infra import get_storage
from infra.storage.base import StorageError
from modules.library.models import LibraryAsset
from modules.template import library_media
from modules.template.authorization import authorization_gap
from modules.template.models import ReplacementRun
from modules.template.workspace import run_path

API = "/api/v1"


async def _library_asset(key: str, content: bytes, *, width: int, height: int) -> str:
    row = LibraryAsset(
        id=uuid.uuid4().hex,
        name=f"v2-fixed-{uuid.uuid4().hex[:8]}.png",
        kind="image",
        origin="upload",
        mime_type="image/png",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=key,
        width=width,
        height=height,
        owner_id=ADMIN,
        visibility="personal",
        state="active",
    )
    async with get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return row.id


async def _drop_library_asset(asset_id: str) -> None:
    async with get_session_factory()() as session:
        row = await session.get(LibraryAsset, asset_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def _run_inputs(run_id: str) -> list[tuple[str, str]]:
    async with get_session_factory()() as session:
        run = await session.get(ReplacementRun, run_id)
        assert run is not None
        from modules.template.repository import RunRepository

        rows = await RunRepository(session).inputs(run_id)
        return [(row.asset_id, row.asset_id_space) for row in rows]


async def _gap(run_id: str) -> str | None:
    async with get_session_factory()() as session:
        run = await session.get(ReplacementRun, run_id)
        assert run is not None
        return await authorization_gap(session, run)


def _template(client, headers, *, source_id: str, pack_id: str) -> str:
    response = client.post(
        f"{API}/templates/video-templates",
        headers=headers,
        json={
            "name": f"v2 固定引用物化 {uuid.uuid4().hex[:6]}",
            "prompt": "对 {{Mixed 1}} 做替换，产品主图为 {{Mixed 2}}。",
            "defaultKind": "product",
            "sourceVideoAssetId": source_id,
            "sourceDurationSec": 2,
            "packId": pack_id,
            "packVersion": 1,
            "rules": {
                "referenceBindings": {
                    "{{Mixed 1}}": "source_video",
                    "{{Mixed 2}}": "front",
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["id"])


def _attach_fixed(client, headers, template_id: str, *, pack_id: str, asset_id: str) -> str:
    prompt = "对 {{Mixed 1}} 做替换，产品主图为 {{Mixed 2}}。\n参考细节 {{Mixed 3}}。"
    response = client.patch(
        f"{API}/templates/video-templates/{template_id}/prompt",
        headers=headers,
        json={
            "prompt": prompt,
            "references": [
                {
                    "token": "{{Mixed 3}}",
                    "packId": pack_id,
                    "packVersion": 1,
                    "assetId": asset_id,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return prompt


def _prepare(client, headers, *, source_id: str, template_id: str, pack_id: str) -> dict:
    response = client.post(
        f"{API}/templates/replacement-runs",
        headers=headers,
        json={
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source_id,
            "videoTemplateId": template_id,
            "packId": pack_id,
            "packVersion": 1,
        },
    )
    return {"status": response.status_code, "body": response.json(), "text": response.text}


def test_prepare_materializes_v2_fixed_reference(client) -> None:
    """v2 固定引用要真的落到 inputs/，摘要、宽高、输入行空间三样都对。"""
    headers = login(client)
    source = upload(client, "v2-fixed-source.mp4", "source", video_bytes(2))
    front = upload(client, "v2-产品主图.png", "product", png_bytes())
    base = asyncio.run(
        add_pack(kind="product", reference_ids=[front["id"]], roles=["front"])
    )
    key = f"tests/template-v2-materialize/{uuid.uuid4().hex}.png"
    content = png_bytes((64, 96))
    asyncio.run(get_storage().put(key, content, "image/png"))
    library_id = asyncio.run(_library_asset(key, content, width=64, height=96))
    detail = asyncio.run(
        add_pack(
            kind="product",
            reference_ids=[library_id],
            roles=["detail"],
            asset_id_space="v2",
        )
    )
    template_id = ""
    try:
        template_id = _template(client, headers, source_id=source["id"], pack_id=base.id)
        prompt = _attach_fixed(
            client, headers, template_id, pack_id=detail.id, asset_id=library_id
        )
        result = _prepare(
            client, headers, source_id=source["id"], template_id=template_id, pack_id=base.id
        )
        assert result["status"] == 201, result["text"]
        run = result["body"]["data"]
        assert run["prompt"] == prompt
        assert [item["id"] for item in run["inputSummary"]["assets"]] == [
            source["id"],
            front["id"],
            library_id,
        ]

        inputs = run_path(run["id"]) / "inputs"
        archived = {
            path.name: path for path in inputs.iterdir() if path.is_file()
        }
        matches = [name for name, path in archived.items() if path.read_bytes() == content]
        assert len(matches) == 1, f"v2 固定引用没有落地：{sorted(archived)}"
        assert matches[0].endswith(".png")
        # 本地驱动零拷贝：不该为同一份字节再下载一份副本
        assert not (inputs / library_media.STAGING_DIR).exists()

        mapping = json.loads((inputs / "input-map.json").read_text(encoding="utf-8"))
        fixed_entry = next(item for item in mapping if item["id"] == library_id)
        assert fixed_entry["sha256"] == hashlib.sha256(content).hexdigest()
        assert fixed_entry["file"] == f"inputs/{matches[0]}"

        media = json.loads(
            (run_path(run["id"]) / "records/media-inputs.json").read_text(encoding="utf-8")
        )
        assert media[f"inputs/{matches[0]}"]["width"] == 64
        assert media[f"inputs/{matches[0]}"]["height"] == 96

        assert dict(asyncio.run(_run_inputs(run["id"])))[library_id] == "v2"
        assert dict(asyncio.run(_run_inputs(run["id"])))[front["id"]] == "v1"
        # 行上的空间若被写成默认 v1，这里会返回「固定引用的参考图已不可用」
        assert asyncio.run(_gap(run["id"])) is None
        # 零拷贝那条路径指向内容寻址的 blob：清理临时副本时绝不能把它删掉
        assert asyncio.run(get_storage().exists(key)) is True
    finally:
        if template_id:
            remove_template(template_id)
        asyncio.run(get_storage().delete(key))
        asyncio.run(drop_pack(detail.id))
        asyncio.run(drop_pack(base.id))
        asyncio.run(_drop_library_asset(library_id))


class _RemoteStorage:
    """只有「远端」行为的存储：没有本地路径，只能分块读。

    真实 S3 驱动就是这么走的，用它才能覆盖「下载进工作区」那条分支——
    本地驱动零拷贝，永远走不到 `iter_chunks()`。
    """

    def __init__(self, blobs: dict[str, bytes], chunk: int = 7) -> None:
        self.blobs = blobs
        self.chunk = chunk
        self.reads: list[str] = []

    def local_path(self, key: str) -> None:
        return None

    def iter_chunks(self, key: str, *, chunk_size: int = 1 << 20) -> AsyncIterator[bytes]:
        if key not in self.blobs:
            raise StorageError(f"对象不存在: {key}")
        self.reads.append(key)

        async def stream() -> AsyncIterator[bytes]:
            data = self.blobs[key]
            step = min(chunk_size, self.chunk)
            for start in range(0, len(data), step):
                yield data[start : start + step]

        return stream()


def test_remote_storage_is_downloaded_then_cleaned(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = login(client)
    source = upload(client, "v2-remote-source.mp4", "source", video_bytes(2))
    front = upload(client, "v2-远端产品主图.png", "product", png_bytes())
    base = asyncio.run(
        add_pack(kind="product", reference_ids=[front["id"]], roles=["front"])
    )
    content = png_bytes((48, 72))
    key = f"library/blobs/cc/{uuid.uuid4().hex}.png"
    remote = _RemoteStorage({key: content})
    monkeypatch.setattr(library_media, "get_storage", lambda: remote)
    library_id = asyncio.run(_library_asset(key, content, width=48, height=72))
    detail = asyncio.run(
        add_pack(
            kind="product",
            reference_ids=[library_id],
            roles=["detail"],
            asset_id_space="v2",
        )
    )
    template_id = ""
    try:
        template_id = _template(client, headers, source_id=source["id"], pack_id=base.id)
        _attach_fixed(client, headers, template_id, pack_id=detail.id, asset_id=library_id)
        result = _prepare(
            client, headers, source_id=source["id"], template_id=template_id, pack_id=base.id
        )
        assert result["status"] == 201, result["text"]
        run = result["body"]["data"]
        assert remote.reads == [key]
        inputs = run_path(run["id"]) / "inputs"
        assert any(
            path.is_file() and path.read_bytes() == content for path in inputs.iterdir()
        )
        # 暂存副本必须清掉：留着会让下一次 prepare 命中「已有归档文件与本次输入不一致」
        staging = inputs / library_media.STAGING_DIR
        assert not staging.exists() or not any(staging.iterdir())
    finally:
        if template_id:
            remove_template(template_id)
        asyncio.run(drop_pack(detail.id))
        asyncio.run(drop_pack(base.id))
        asyncio.run(_drop_library_asset(library_id))


def test_missing_v2_bytes_fail_with_actionable_message(client) -> None:
    """发布后字节被删：报「哪张图缺字节」，不是 500，也不是静默少一张。"""
    headers = login(client)
    source = upload(client, "v2-missing-source.mp4", "source", video_bytes(2))
    front = upload(client, "v2-缺字节产品主图.png", "product", png_bytes())
    base = asyncio.run(
        add_pack(kind="product", reference_ids=[front["id"]], roles=["front"])
    )
    key = f"tests/template-v2-missing/{uuid.uuid4().hex}.png"
    content = png_bytes((32, 32))
    asyncio.run(get_storage().put(key, content, "image/png"))
    library_id = asyncio.run(_library_asset(key, content, width=32, height=32))
    detail = asyncio.run(
        add_pack(
            kind="product",
            reference_ids=[library_id],
            roles=["detail"],
            asset_id_space="v2",
        )
    )
    template_id = ""
    try:
        template_id = _template(client, headers, source_id=source["id"], pack_id=base.id)
        _attach_fixed(client, headers, template_id, pack_id=detail.id, asset_id=library_id)
        asyncio.run(get_storage().delete(key))
        result = _prepare(
            client, headers, source_id=source["id"], template_id=template_id, pack_id=base.id
        )
        assert result["status"] == 400, result["text"]
        assert "字节缺失" in result["body"]["message"]
        assert "v2-fixed-" in result["body"]["message"]
    finally:
        if template_id:
            remove_template(template_id)
        asyncio.run(drop_pack(detail.id))
        asyncio.run(drop_pack(base.id))
        asyncio.run(_drop_library_asset(library_id))


def test_unknown_library_mime_is_rejected_before_download(tmp_path) -> None:
    """扩展名认不出来就当场拒绝：猜一个 `.bin` 会让供应商那头把类型当未知。"""
    ref = LibraryAssetRef(
        id=uuid.uuid4().hex,
        owner_id=ADMIN,
        kind=LibraryAssetKind.IMAGE,
        name="奇怪格式",
        state=LibraryAssetState.ACTIVE,
        visibility=LibraryVisibility.PERSONAL,
        storage_key="library/blobs/aa/deadbeef",
        mime_type="application/x-unknown",
    )
    with pytest.raises(Exception, match="格式无法识别"):
        asyncio.run(library_media.stage_library_refs(tmp_path, {"t": ref}))


def test_partial_failure_cleans_what_was_already_downloaded(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二张取不到时，第一张刚下载的副本不能留在工作区。

    调用方拿到的是异常，手里没有 `staged`，替这里清理是不可能的事。
    """
    good_key = "library/blobs/aa/good.png"
    bad_key = "library/blobs/bb/gone.png"
    remote = _RemoteStorage({good_key: png_bytes()})
    monkeypatch.setattr(library_media, "get_storage", lambda: remote)

    def ref(key: str, name: str) -> LibraryAssetRef:
        return LibraryAssetRef(
            id=uuid.uuid4().hex,
            owner_id=ADMIN,
            kind=LibraryAssetKind.IMAGE,
            name=name,
            state=LibraryAssetState.ACTIVE,
            visibility=LibraryVisibility.PERSONAL,
            storage_key=key,
            mime_type="image/png",
        )

    with pytest.raises(BadRequest) as caught:
        asyncio.run(
            library_media.stage_library_refs(
                tmp_path, {"a": ref(good_key, "在的图"), "b": ref(bad_key, "丢的图")}
            )
        )
    assert "丢的图" in str(caught.value)
    staging = tmp_path / "inputs" / library_media.STAGING_DIR
    assert not staging.exists() or not any(staging.iterdir())
