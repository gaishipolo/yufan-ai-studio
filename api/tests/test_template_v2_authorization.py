"""模板三道授权闸的 v2 分流：可读性、生命周期与对象存储字节。

`CURRENT_ASSET_ID_SPACE` 仍为 v1，所以草稿准入用例只在函数级临时切换写入空间；
冻结版本与运行输入则用例级显式写 `v2`，验证历史行不会被当前常量重新解释。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from tests_template_helpers import (
    ADMIN,
    add_input,
    add_pack,
    add_run,
    drop_pack,
    drop_run,
)

from core.context import CurrentUser
from core.exceptions import BadRequest, NotFound
from db.session import get_session_factory
from infra import get_storage
from modules.asset.models import Asset
from modules.library.models import LibraryAsset, LibraryPackItem
from modules.template import pack_service
from modules.template.authorization import authorization_gap
from modules.template.id_space import ASSET_ID_SPACE_V2
from modules.template.models import ReplacementRun
from modules.template.pack_service import PackService
from modules.template.repository import PackRepository
from modules.template.schema import PackDraftIn
from modules.template.template_references import resolve_pack_references
from modules.template.video_template_schema import VideoTemplatePackReferenceIn

CONSUMER = "template-v2-consumer"
PACK_OWNER = "template-v2-pack-owner"
INPUT_GONE = "本次输入的素材已被删除，请重新准备"


async def _asset(
    owner_id: str,
    *,
    kind: str = "image",
    state: str = "active",
    visibility: str = "personal",
    storage_key: str = "",
) -> str:
    row = LibraryAsset(
        id=uuid.uuid4().hex,
        name=f"{kind}-{uuid.uuid4().hex[:8]}",
        kind=kind,
        origin="upload",
        mime_type="video/mp4" if kind == "video" else "image/png",
        storage_key=storage_key,
        owner_id=owner_id,
        visibility=visibility,
        state=state,
    )
    async with get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return row.id


async def _drop_assets(asset_ids: list[str]) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            delete(LibraryPackItem).where(
                (LibraryPackItem.pack_id.in_(asset_ids))
                | (LibraryPackItem.asset_id.in_(asset_ids))
            )
        )
        await session.execute(delete(LibraryAsset).where(LibraryAsset.id.in_(asset_ids)))
        await session.commit()


def _draft(reference_ids: list[str]) -> PackDraftIn:
    return PackDraftIn.model_validate(
        {
            "requestId": uuid.uuid4(),
            "kind": "character",
            "name": "v2 授权测试",
            "referenceIds": reference_ids,
        }
    )


def _fixed(pack_id: str, asset_id: str) -> VideoTemplatePackReferenceIn:
    return VideoTemplatePackReferenceIn.model_validate(
        {
            "token": "{{Mixed 2}}",
            "packId": pack_id,
            "packVersion": 1,
            "assetId": asset_id,
        }
    )


async def _gap(run_id: str) -> str | None:
    async with get_session_factory()() as session:
        run = await session.get(ReplacementRun, run_id)
        assert run is not None
        return await authorization_gap(session, run)


@pytest.mark.parametrize(
    ("owner_id", "state", "kind", "visibility", "allowed"),
    [
        (CONSUMER, "active", "image", "personal", True),
        (PACK_OWNER, "active", "image", "team", True),
        (PACK_OWNER, "active", "image", "personal", False),
        (CONSUMER, "draft", "image", "personal", False),
        (CONSUMER, "trash", "image", "personal", False),
        (CONSUMER, "active", "video", "personal", False),
    ],
)
async def test_draft_gate_requires_active_readable_v2_image(
    monkeypatch: pytest.MonkeyPatch,
    owner_id: str,
    state: str,
    kind: str,
    visibility: str,
    allowed: bool,
) -> None:
    monkeypatch.setattr(pack_service, "CURRENT_ASSET_ID_SPACE", ASSET_ID_SPACE_V2)
    asset_id = await _asset(owner_id, state=state, kind=kind, visibility=visibility)
    try:
        async with get_session_factory()() as session:
            if allowed:
                await PackService(session)._check_references(_draft([asset_id]), CONSUMER)
            else:
                with pytest.raises(BadRequest, match="只能封装可读的已采纳参考图片"):
                    await PackService(session)._check_references(_draft([asset_id]), CONSUMER)
    finally:
        await _drop_assets([asset_id])


async def test_draft_gate_reuses_pack_member_read_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员图仍是私有的；可读封装共享后，消费者可读成员，撤销后立即失效。"""
    monkeypatch.setattr(pack_service, "CURRENT_ASSET_ID_SPACE", ASSET_ID_SPACE_V2)
    image_id = await _asset(PACK_OWNER)
    container_id = await _asset(PACK_OWNER, kind="pack", visibility="team")
    async with get_session_factory()() as session:
        session.add(LibraryPackItem(pack_id=container_id, asset_id=image_id, position=1))
        await session.commit()
    try:
        async with get_session_factory()() as session:
            assets = await pack_service.resolve_assets(
                session,
                [(image_id, ASSET_ID_SPACE_V2)],
                actor=CurrentUser(id=CONSUMER, name=CONSUMER),
            )
            assert assets.readable_v2 == frozenset({image_id})
            await PackService(session)._check_references(_draft([image_id]), CONSUMER)
            container = await session.get(LibraryAsset, container_id)
            assert container is not None
            container.visibility = "personal"
            await session.commit()
        async with get_session_factory()() as session:
            revoked = await pack_service.resolve_assets(
                session,
                [(image_id, ASSET_ID_SPACE_V2)],
                actor=CurrentUser(id=CONSUMER, name=CONSUMER),
            )
            assert revoked.readable_v2 == frozenset()
            with pytest.raises(BadRequest, match="只能封装可读的已采纳参考图片"):
                await PackService(session)._check_references(_draft([image_id]), CONSUMER)
    finally:
        await _drop_assets([container_id, image_id])


async def test_publish_uses_reference_space_and_checks_v2_object_bytes() -> None:
    key = f"tests/template-v2/{uuid.uuid4().hex}.png"
    asset_id = await _asset(ADMIN, storage_key=key)
    pack = await add_pack(reference_ids=[asset_id], status="draft", asset_id_space="v2")
    try:
        await get_storage().put(key, b"v2-image-bytes", "image/png")
        async with get_session_factory()() as session:
            service = PackService(session)
            published = await service.publish(pack.id, ADMIN, pack.revision)
            assert published.version == 1
            frozen = await PackRepository(session).get_version(pack.id, 1)
            assert frozen is not None
            assert frozen.reference_ids_space == ASSET_ID_SPACE_V2
            with pytest.raises(NotFound, match="包内预览暂不可用"):
                await service.preview(pack.id, asset_id, 1)

        await get_storage().delete(key)
        async with get_session_factory()() as session:
            refs = await PackRepository(session).references(pack.id)
            with pytest.raises(BadRequest, match="素材字节缺失"):
                await PackService(session)._verify_bytes(refs)
    finally:
        await get_storage().delete(key)
        await drop_pack(pack.id)
        await _drop_assets([asset_id])


async def test_v2_marker_never_falls_back_to_v1_bytes() -> None:
    legacy_id = uuid.uuid4().hex
    async with get_session_factory()() as session:
        session.add(
            Asset(
                id=legacy_id,
                name="同 id 的 v1 图",
                type="image",
                category="character",
                owner_id=ADMIN,
                storage_key="blobs/legacy/still-must-not-be-read.png",
            )
        )
        await session.commit()
    pack = await add_pack(reference_ids=[legacy_id], asset_id_space="v2")
    try:
        async with get_session_factory()() as session:
            refs = await PackRepository(session).references(pack.id)
            with pytest.raises(BadRequest, match="素材字节缺失"):
                await PackService(session)._verify_bytes(refs)
    finally:
        await drop_pack(pack.id)
        async with get_session_factory()() as session:
            await session.execute(delete(Asset).where(Asset.id == legacy_id))
            await session.commit()


async def test_fixed_reference_resolver_uses_frozen_space_and_current_actor() -> None:
    asset_id = await _asset(PACK_OWNER, visibility="team")
    pack = await add_pack(owner=PACK_OWNER, reference_ids=[asset_id], asset_id_space="v2")
    actor = CurrentUser(id=CONSUMER, name=CONSUMER)
    try:
        async with get_session_factory()() as session:
            shown, legacy, library = await resolve_pack_references(
                session, [_fixed(pack.id, asset_id)], actor
            )
            assert shown[0]["name"].startswith("image-")
            assert legacy == {}
            assert set(library) == {"{{Mixed 2}}"}
        async with get_session_factory()() as session:
            asset = await session.get(LibraryAsset, asset_id)
            assert asset is not None
            asset.visibility = "personal"
            await session.commit()
        async with get_session_factory()() as session:
            with pytest.raises(BadRequest, match="封装参考图不可用"):
                await resolve_pack_references(session, [_fixed(pack.id, asset_id)], actor)
    finally:
        await drop_pack(pack.id)
        await _drop_assets([asset_id])


@pytest.mark.parametrize(
    ("state", "kind", "visibility"),
    [
        ("active", "image", "team"),
        ("active", "image", "personal"),
        ("draft", "image", "personal"),
        ("trash", "image", "personal"),
        ("active", "video", "team"),
    ],
)
async def test_execute_gate_rechecks_v2_fixed_reference(
    state: str, kind: str, visibility: str
) -> None:
    asset_id = await _asset(PACK_OWNER, state=state, kind=kind, visibility=visibility)
    pack = await add_pack(owner=PACK_OWNER, reference_ids=[asset_id], asset_id_space="v2")
    run = await add_run(
        owner=CONSUMER,
        configuration={
            "fixed_references": [
                {
                    "token": "{{Mixed 2}}",
                    "packId": pack.id,
                    "packVersion": 1,
                    "assetId": asset_id,
                }
            ]
        },
    )
    try:
        gap = await _gap(run.id)
        if state == "active" and kind == "image" and visibility == "team":
            assert gap is None
        else:
            assert gap == "固定引用的参考图已不可用，请重新准备"
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)
        await _drop_assets([asset_id])


@pytest.mark.parametrize(
    ("role", "owner_id", "state", "kind", "visibility", "expected"),
    [
        ("source_video", CONSUMER, "active", "video", "personal", None),
        ("source_video", PACK_OWNER, "active", "video", "team", None),
        ("source_video", PACK_OWNER, "active", "video", "personal", "原片不存在或已停止共享"),
        ("reference", PACK_OWNER, "active", "image", "team", None),
        ("reference", CONSUMER, "draft", "image", "personal", INPUT_GONE),
        ("reference", CONSUMER, "trash", "image", "personal", INPUT_GONE),
        ("reference", CONSUMER, "active", "video", "personal", INPUT_GONE),
        ("reference", PACK_OWNER, "active", "image", "personal", INPUT_GONE),
    ],
)
async def test_execute_gate_rechecks_each_v2_run_input(
    role: str,
    owner_id: str,
    state: str,
    kind: str,
    visibility: str,
    expected: str | None,
) -> None:
    asset_id = await _asset(owner_id, state=state, kind=kind, visibility=visibility)
    run = await add_run(owner=CONSUMER)
    await add_input(run.id, asset_id, role=role, asset_id_space="v2")
    try:
        assert await _gap(run.id) == expected
    finally:
        await drop_run(run.id)
        await _drop_assets([asset_id])


async def test_v2_run_input_never_falls_back_to_v1_row() -> None:
    legacy_id = uuid.uuid4().hex
    async with get_session_factory()() as session:
        session.add(
            Asset(
                id=legacy_id,
                name="只存在 v1 的原片",
                type="video",
                category="source",
                owner_id=CONSUMER,
            )
        )
        await session.commit()
    run = await add_run(owner=CONSUMER)
    await add_input(run.id, legacy_id, role="source_video", asset_id_space="v2")
    try:
        assert await _gap(run.id) == INPUT_GONE
    finally:
        await drop_run(run.id)
        async with get_session_factory()() as session:
            await session.execute(delete(Asset).where(Asset.id == legacy_id))
            await session.commit()
