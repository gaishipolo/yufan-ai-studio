"""资产库 v2 固定引用的生成物化：从对象存储搬进本次 run 的工作区。

这一层只回答「字节从哪来」，权限在 `authorization` / `readable_ids()` 已经判完。
三条判据都要钉住，否则失败方向都是**静默出片**：

1. 字节必须真的进工作区（`input-map.json` 的摘要与实算一致，执行器才肯上传）；
2. 宽高要带过去（`media-inputs.json` 少了它，供应商那头把图当未知尺寸）；
3. 三条来源都要落地——原片、包内参考图、封装外的固定引用各走各的分支，
   少一条时编译器仍能产出提示词，执行器要到上传阶段才发现 mixedList 缺项。
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


async def _library_video(
    key: str, content: bytes, *, duration: float, has_audio: bool = True
) -> str:
    """造一条 v2 原片。走上传接口会被 ffprobe 拦（假字节不是真 mp4）。"""
    row = LibraryAsset(
        id=uuid.uuid4().hex,
        name=f"v2-source-{uuid.uuid4().hex[:8]}.mp4",
        kind="video",
        origin="upload",
        mime_type="video/mp4",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=key,
        duration=duration,
        has_audio=has_audio,
        width=1080,
        height=1920,
        owner_id=ADMIN,
        visibility="personal",
        state="active",
    )
    async with get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return row.id


async def _storage_key(asset_id: str) -> str:
    """资产库行上的 `storage_key`：远端驱动只认自己手里那几份，得按它铺数据。"""
    async with get_session_factory()() as session:
        row = await session.get(LibraryAsset, asset_id)
        assert row is not None
        return row.storage_key


async def _run_inputs(run_id: str) -> list[str]:
    """这条 run 的输入清单（按落库顺序）。

    v1 下线后 `asset_id` 只剩 v2 一个解释，行上不再有空间标记可断言——这里
    能验证的是「该落进来的 id 都进来了」，字节是否真搬进工作区由各用例自己看。
    """
    async with get_session_factory()() as session:
        run = await session.get(ReplacementRun, run_id)
        assert run is not None
        from modules.template.repository import RunRepository

        rows = await RunRepository(session).inputs(run_id)
        return [row.asset_id for row in rows]


async def _gap(run_id: str) -> str | None:
    async with get_session_factory()() as session:
        run = await session.get(ReplacementRun, run_id)
        assert run is not None
        return await authorization_gap(session, run)


def _run_spec(run_id: str) -> dict:
    """编译输入快照：时长这类「算出来的值」只在这里，出参不重复暴露。"""
    async def read() -> dict:
        async with get_session_factory()() as session:
            run = await session.get(ReplacementRun, run_id)
            assert run is not None
            return dict(run.prompt_spec or {})

    return asyncio.run(read())


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
            "sourceLibraryId": source_id,
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

        input_ids = asyncio.run(_run_inputs(run["id"]))
        assert library_id in input_ids
        assert front["id"] in input_ids
        # 固定引用被登记成输入行，复核时才能发现「封面上那张图后来被删了」
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
    video = video_bytes(2)
    source = upload(client, "v2-remote-source.mp4", "source", video)
    picture = png_bytes()
    front = upload(client, "v2-远端产品主图.png", "product", picture)
    base = asyncio.run(
        add_pack(kind="product", reference_ids=[front["id"]], roles=["front"])
    )
    content = png_bytes((48, 72))
    key = f"library/blobs/cc/{uuid.uuid4().hex}.png"
    remote = _RemoteStorage({key: content})
    # 原片与包内参考图现在也都在同一个对象存储里，远端驱动拿不到的 key 就是
    # 「字节缺失」。按行上的 key 把实传字节铺进去，才算一个真实的远端现场。
    remote.blobs[asyncio.run(_storage_key(source["id"]))] = video
    remote.blobs[asyncio.run(_storage_key(front["id"]))] = picture
    monkeypatch.setattr(library_media, "get_storage", lambda: remote)
    library_id = asyncio.run(_library_asset(key, content, width=48, height=72))
    detail = asyncio.run(
        add_pack(
            kind="product",
            reference_ids=[library_id],
            roles=["detail"],
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
        # 固定引用走的是「分块下载」那条分支（本地驱动零拷贝，永远走不到）
        assert key in remote.reads
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


def test_prepare_accepts_v2_source_video(client) -> None:
    """v2 原片走 `sourceLibraryId`：字节落成 `inputs/reference-video.mp4`，输入行记 v2。

    这是整条链上最容易「看起来成功」的一处：原片没落盘时编译器仍能产出提示词，
    执行器要到上传阶段才发现 mixedList 第一项没有文件。
    """
    headers = login(client)
    # 尾部加一段随机字节：`library_assets` 有 (owner, sha256, kind) 唯一约束，
    # 同一条 mp4 造两份会直接撞键。prepare 只搬字节、不解析容器，尾部多余数据无影响。
    content = video_bytes(2) + uuid.uuid4().bytes
    key = f"library/blobs/af/{uuid.uuid4().hex}.mp4"
    asyncio.run(get_storage().put(key, content, "video/mp4"))
    source_id = asyncio.run(_library_video(key, content, duration=2.0))
    front = upload(client, "v2-源片-产品主图.png", "product", png_bytes())
    pack = asyncio.run(
        add_pack(kind="product", reference_ids=[front["id"]], roles=["front"], scopes=["worn"])
    )
    try:
        response = client.post(
            f"{API}/templates/replacement-runs",
            headers=headers,
            json={
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "sourceLibraryId": source_id,
                "packId": pack.id,
                "packVersion": 1,
                "replacementScope": "人物上身穿着",
            },
        )
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        inputs = run_path(run["id"]) / "inputs"
        assert (inputs / "reference-video.mp4").read_bytes() == content
        # 原片与参考图都进了输入清单，字节也都落了盘
        input_ids = asyncio.run(_run_inputs(run["id"]))
        assert source_id in input_ids
        assert front["id"] in input_ids
        # 时长取自 `duration_ms`，不是 ffprobe 现算——原片可能已被移出资产库
        assert _run_spec(run["id"])["duration"] == 2
        assert asyncio.run(_gap(run["id"])) is None
    finally:
        asyncio.run(drop_pack(pack.id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_library_asset(source_id))


def test_prepare_accepts_pack_made_of_v2_references(client) -> None:
    """整包都是 v2 图的封装：prepare 要按发布版本的空间取字节，不能回旧表找。

    这一条是「资产库封装能被模板调用」的落点。`referenceIds` 里装的其实是
    `frozen.reference_ids` —— 形状与 v1 id 完全一样，只有
    `template_pack_versions.reference_ids_space` 说得清该去哪张表取。判错方向是
    静默的：报「参考图不存在或已删除」，用户以为自己选的封装坏了。
    """
    headers = login(client)
    source = upload(client, "v2-整包-原片.mp4", "source", video_bytes(2))
    content = png_bytes((80, 120))
    key = f"library/blobs/ba/{uuid.uuid4().hex}.png"
    asyncio.run(get_storage().put(key, content, "image/png"))
    library_id = asyncio.run(_library_asset(key, content, width=80, height=120))
    pack = asyncio.run(
        add_pack(
            kind="product",
            reference_ids=[library_id],
            roles=["front"],
            scopes=["worn"],
        )
    )
    try:
        response = client.post(
            f"{API}/templates/replacement-runs",
            headers=headers,
            json={
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "sourceLibraryId": source["id"],
                "packId": pack.id,
                "packVersion": 1,
                "replacementScope": "人物上身穿着",
            },
        )
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        inputs = run_path(run["id"]) / "inputs"
        archived = [path for path in inputs.iterdir() if path.is_file()]
        assert any(path.read_bytes() == content for path in archived), sorted(
            path.name for path in archived
        )
        # 包内参考图不是固定引用：它不该出现在 `fixedReferenceTokens` 里，
        # 否则编译器会把它当 `{{Mixed N}}` 再插一遍，编号整体错位。
        spec = _run_spec(run["id"])
        assert not spec.get("fixedReferenceTokens")
        input_ids = asyncio.run(_run_inputs(run["id"]))
        assert library_id in input_ids
        assert source["id"] in input_ids
        assert asyncio.run(_gap(run["id"])) is None
    finally:
        asyncio.run(drop_pack(pack.id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_library_asset(library_id))


def test_v2_pack_with_trashed_reference_is_rejected(client) -> None:
    """发布后素材进了回收站：prepare 当场拒绝，不能拿一条已删除的图去出片。

    判据是 `state == active`（生命周期），不是再写一套共享规则——可读性已经由
    `readable_ids()` 放行（本人永远可读自己的回收站条目），所以这条必须单独拦。
    """
    headers = login(client)
    source = upload(client, "v2-回收站-原片.mp4", "source", video_bytes(2))
    content = png_bytes((40, 40))
    key = f"library/blobs/bb/{uuid.uuid4().hex}.png"
    asyncio.run(get_storage().put(key, content, "image/png"))
    library_id = asyncio.run(_library_asset(key, content, width=40, height=40))
    pack = asyncio.run(
        add_pack(
            kind="product",
            reference_ids=[library_id],
            roles=["front"],
            scopes=["worn"],
        )
    )

    async def trash() -> None:
        async with get_session_factory()() as session:
            row = await session.get(LibraryAsset, library_id)
            assert row is not None
            row.state = "trash"
            await session.commit()

    try:
        asyncio.run(trash())
        response = client.post(
            f"{API}/templates/replacement-runs",
            headers=headers,
            json={
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "sourceLibraryId": source["id"],
                "packId": pack.id,
                "packVersion": 1,
                "replacementScope": "人物上身穿着",
            },
        )
        assert response.status_code == 400, response.text
        assert "有效的图片" in response.json()["message"]
    finally:
        asyncio.run(drop_pack(pack.id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_library_asset(library_id))


def test_source_video_must_be_a_short_clip_with_audio(client) -> None:
    """原片得是带音轨的 1–15 秒视频；缺字段直接拒，别让它跑成一条静音废片。"""
    headers = login(client)
    plain_video = video_bytes(2)
    good_content = plain_video + uuid.uuid4().bytes
    silent_content = plain_video + uuid.uuid4().bytes
    good_key = f"library/blobs/b0/{uuid.uuid4().hex}.mp4"
    silent_key = f"library/blobs/b1/{uuid.uuid4().hex}.mp4"
    asyncio.run(get_storage().put(good_key, good_content, "video/mp4"))
    asyncio.run(get_storage().put(silent_key, silent_content, "video/mp4"))
    muted = asyncio.run(_library_video(silent_key, silent_content, duration=2.0, has_audio=False))
    long_one = asyncio.run(_library_video(good_key, good_content, duration=30.0))
    front = upload(client, "字段互斥-产品主图.png", "product", png_bytes())
    pack = asyncio.run(
        add_pack(kind="product", reference_ids=[front["id"]], roles=["front"], scopes=["worn"])
    )

    def prepare(**extra) -> dict:
        body = {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "packId": pack.id,
            "packVersion": 1,
            "replacementScope": "人物上身穿着",
            **extra,
        }
        response = client.post(f"{API}/templates/replacement-runs", headers=headers, json=body)
        return {"status": response.status_code, "body": response.json(), "text": response.text}

    try:
        neither = prepare()
        assert neither["status"] == 400, neither["text"]
        assert "请选择要替换的原片" in neither["body"]["message"]

        no_audio = prepare(sourceLibraryId=muted)
        assert no_audio["status"] == 400, no_audio["text"]
        assert "带音轨" in no_audio["body"]["message"]

        too_long = prepare(sourceLibraryId=long_one)
        assert too_long["status"] == 400, too_long["text"]
        assert "1–15 秒" in too_long["body"]["message"]
    finally:
        asyncio.run(drop_pack(pack.id))
        asyncio.run(get_storage().delete(good_key))
        asyncio.run(get_storage().delete(silent_key))
        asyncio.run(_drop_library_asset(muted))
        asyncio.run(_drop_library_asset(long_one))

