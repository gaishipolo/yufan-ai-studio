"""`template-runtime` 端口：替换执行链读封装数据的唯一通道。

四件事各守一处：

1. **未注册时 fail-closed**（抛 `RegistryError`）：执行链拿不到冻结版本就准备不了
   任务，退化成「封装不存在」的 400 会把装配故障说成用户选错包；
2. **冻结版本原样带出**：`reference_ids` 与 `bindings` 必须按位置对齐、顺序不变，
   那是 `{{Mixed N}}` 的编号依据；没发布过的版号返回 `None`；
3. **模板快照只映射执行时会读到的字段**（`rules` / `prompt` / `media_order`…），
   并且 `media_order` 换成 core 层的不可变项，执行链不再接触业务出参模型；
4. **目录投影仍是 camelCase**：它是封装模块自己的对外形状，消费方用自己的
   出参模型校验一次（`ReplacementPack.model_validate`）。

为什么这些用例不直接调仓储：端口的全部意义就是「搬迁时消费方一行不改」，所以
断言必须落在**端口返回的快照**上，而不是某张表的字段。
"""

from __future__ import annotations

import pytest
from tests_template_helpers import add_pack, drop_pack
from video_template_support import IMAGES, PACK_ID, aput

from core.exceptions import BadRequest, RegistryError
from core.ports.template_runtime import (
    TemplateRuntimePort,
    get_template_runtime,
    has_template_runtime,
    register_template_runtime,
    require_template_runtime,
    reset_template_runtime,
)
from db.base import utcnow
from db.session import get_session_factory
from modules.template.models import TemplatePack
from modules.template.runtime_port_impl import TemplateRuntime


def test_module_registers_an_implementation() -> None:
    """`modules.template` 在 import 期注册；形状必须是端口协议本身。"""
    assert has_template_runtime()
    runtime = get_template_runtime()
    assert runtime is not None
    assert isinstance(runtime, TemplateRuntimePort)
    assert isinstance(runtime, TemplateRuntime)


def test_unregistered_port_fails_closed() -> None:
    """没装配就是 500，不是「没有封装」：两者让用户做的事完全不同。"""
    reset_template_runtime()
    try:
        assert not has_template_runtime()
        assert get_template_runtime() is None
        with pytest.raises(RegistryError, match="template-runtime"):
            require_template_runtime()
    finally:
        # 端口是进程级单例：不复位会让同一场测试里后面所有用例都拿不到实现。
        register_template_runtime(TemplateRuntime())


async def test_pack_snapshot_carries_the_frozen_version() -> None:
    pack = await add_pack(
        kind="product",
        name="端口测试包",
        reference_ids=["ref-a", "ref-b"],
        roles=["front", "multiview"],
        features="特征说明",
        instructions="补充说明",
        scopes=["top"],
    )
    try:
        async with get_session_factory()() as session:
            runtime = require_template_runtime()
            head = await runtime.get_pack(session, pack.id)
            assert head is not None
            assert (head.id, head.name, head.kind, head.status) == (
                pack.id,
                "端口测试包",
                "product",
                "published",
            )
            assert head.version == 1
            assert head.deleted is False

            frozen = await runtime.get_pack_version(session, pack.id, 1)
            assert frozen is not None
            # 顺序即 `{{Mixed N}}`：不能被集合化、不能重排。
            assert frozen.reference_ids == ("ref-a", "ref-b")
            assert [item["role"] for item in frozen.bindings] == ["front", "multiview"]
            assert frozen.supported_scopes == ("top",)
            assert (frozen.features, frozen.instructions) == ("特征说明", "补充说明")
            assert frozen.wardrobe_mode == "preserve"

            # 没发布过的版号是 `None`（调用方据此报「版本不可用」），不是空快照。
            assert await runtime.get_pack_version(session, pack.id, 2) is None
            assert await runtime.get_pack(session, "no-such-pack") is None
    finally:
        await drop_pack(pack.id)


async def test_deleted_pack_is_reported_as_deleted_not_missing() -> None:
    """软删除要如实带 `deleted=True`：调用方靠它区分「回收站里」与「根本不存在」。"""
    pack = await add_pack()
    try:
        async with get_session_factory()() as session:
            stored = await session.get(TemplatePack, pack.id)
            assert stored is not None
            stored.deleted_at = utcnow()
            await session.commit()

            head = await require_template_runtime().get_pack(session, pack.id)
            assert head is not None
            assert head.deleted is True
    finally:
        await drop_pack(pack.id)


async def test_template_snapshot_maps_only_runtime_fields() -> None:
    # 独立原片 id：测试库整场共用，别的用例留下的蓝本会抢先命中同一原片。
    source = "runtime-port-source"
    out = await aput(
        id="runtime-port-template",
        templateKey="runtime-port-template",
        sourceVideoAssetId=source,
        pinned=False,
    )
    async with get_session_factory()() as session:
        runtime = require_template_runtime()
        found = await runtime.find_template_for_run(
            session, source_asset_id=source, pack_id=PACK_ID
        )
        assert found is not None
        assert (found.id, found.name) == (out.id, out.name)
        assert found.prompt == out.prompt
        assert found.prompt_sha256 == out.prompt_sha256
        assert found.rules == out.rules
        assert found.default_kind == "product"
        assert found.aspect_ratio == "9:16"
        assert found.generation == {"duration": 5, "ratio": "9:16"}
        # 媒体槽位换成 core 层的不可变项，字段与执行链读的那四个一一对应。
        assert found.media_order[0].type == "video"
        assert found.media_order[0].role == "source_video"
        assert [item.asset_id for item in found.media_order[1:]] == list(IMAGES)

        # 原片对不上就是「这条原片没有模板」，不是异常。
        assert (
            await runtime.find_template_for_run(
                session, source_asset_id="someone-else", pack_id=PACK_ID
            )
            is None
        )
        # 显式指定模板却对不上原片：用户在表单里选错了，必须当场拒绝。
        with pytest.raises(BadRequest):
            await runtime.find_template_for_run(
                session,
                source_asset_id="someone-else",
                pack_id=PACK_ID,
                template_id=out.id,
            )


async def test_template_names_batch_lookup() -> None:
    """老档案只有模板 id：名字要能批量反查，查不到的**不出现**在结果里。"""
    await aput(id="runtime-port-named", templateKey="runtime-port-named", name="端口命名模板",
               pinned=False)
    async with get_session_factory()() as session:
        names = await require_template_runtime().template_names(
            session, ["runtime-port-named", "runtime-port-missing"]
        )
    assert names == {"runtime-port-named": "端口命名模板"}


async def test_available_packs_projection_stays_camel_case() -> None:
    """目录投影是封装模块自己的对外形状：消费方用自己的出参模型校验一次。"""
    from modules.template.schema import ReplacementPack

    pack = await add_pack(
        kind="character",
        name="目录投影包",
        reference_ids=["card-front"],
        roles=["front"],
    )
    try:
        async with get_session_factory()() as session:
            rows = await require_template_runtime().available_packs(session)
        payload = next(item for item in rows if item["id"] == pack.id)
        assert payload["referenceIds"] == ["card-front"]
        assert "coverUrl" in payload

        card = ReplacementPack.model_validate(payload)
        assert card.kind == "character"
        assert card.reference_ids == ["card-front"]
        # 假素材取不到字节：仍然出现在目录里，但如实标成不可选。
        assert card.available is False
    finally:
        await drop_pack(pack.id)


async def test_fixed_reference_outside_the_frozen_version_is_rejected() -> None:
    """图片不在该发布版本里 → `BadRequest`（形状与归属都由封装侧判）。"""
    pack = await add_pack(reference_ids=["in-version"], roles=["front"])
    try:
        async with get_session_factory()() as session:
            with pytest.raises(BadRequest, match="引用的封装"):
                await require_template_runtime().resolve_fixed_references(
                    session,
                    [
                        {
                            "token": "{{Mixed 2}}",
                            "packId": pack.id,
                            "packVersion": 1,
                            "assetId": "not-in-version",
                        }
                    ],
                    actor=None,  # type: ignore[arg-type]  校验在取素材之前就失败了
                )
    finally:
        await drop_pack(pack.id)
