"""封装草稿：逐图用途、缺项清单、幂等保存与封面归属。

这个文件合并了重建前的两份用例（`test_pack_roles.py` 与 `test_replacement_pack_cover.py`），
因为它们的现场是同一个——「运营在封装编辑器里点保存」——只是断言的东西不同。
重建后现场唯一的变化：草稿不再是 `pack_drafts/*.json`，而是 `template_packs` 一行，
所以 `PackService` 要带 session，`save` / `publish` 都是 async。

**保留的判据一条都没变**：

1. `gaps()` 的顺序就是封装者该补的顺序（前端直接当待办清单渲染）；
2. 旧包（只有 `reference_ids`、没有用途）读出来必须「每张都能用」；
3. 同一次提交因超时重发时不能报「草稿已变化」；
4. 封面只在包内引用里取，不认「曾经的素材 id」。
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import ADMIN, actor_id, add_pack, drop_pack, png_bytes, upload

from core.exceptions import BadRequest, Conflict, NotFound
from db.session import get_session_factory
from modules.template.pack_roles import PACK_ROLES, PACK_SCOPES
from modules.template.pack_service import PackService
from modules.template.repository import PackRepository
from modules.template.schema import PackDraftIn

KINDS = ("character", "product", "scene")


def _service():
    """每次业务调用一个新 session：与「一个 HTTP 请求一个 session」同形。

    共用一个 session 会让 `save` 之后的 `publish` 落进同一个未关闭的事务，
    `create_version` 的 savepoint 语义就测不到了。
    """
    return get_session_factory()()


async def _register(client: TestClient, kind: str, count: int) -> list[str]:
    """走真实上传登记参考图：准入权限（`asset:reference:manage`）只在这条路上生效。"""
    return [
        upload(client, f"{kind}-{index}.png", kind, png_bytes())["id"]
        for index in range(count)
    ]


def _payload(kind: str, ids: list[str], **extra: Any) -> PackDraftIn:
    values: dict[str, Any] = {
        "requestId": uuid.uuid4(),
        "kind": kind,
        "name": "测试对象",
        "referenceLibraryIds": ids,
        "coverLibraryId": ids[0] if ids else None,
    }
    values.update(extra)
    return PackDraftIn.model_validate(values)


async def _save(payload: PackDraftIn, owner: str = ADMIN):
    async with _service() as session:
        return await PackService(session).save(payload, owner)


async def _publish(pack_id: str, revision: int, owner: str = ADMIN):
    async with _service() as session:
        return await PackService(session).publish(pack_id, owner, revision)


async def _available() -> list[dict]:
    async with _service() as session:
        return [pack.model_dump() for pack in await PackService(session).available()]


# --------------------------------------------------------------------------- #
# 用途与缺项
# --------------------------------------------------------------------------- #
async def test_save_keeps_roles_and_reports_gaps(client: TestClient) -> None:
    owner = actor_id(client)
    ids = await _register(client, "product", 2)
    saved = await _save(
        _payload(
            "product",
            ids,
            bindings=[{"role": "front"}, {"role": "multiview"}],
            requiredRoles=["detail"],
        ),
        owner,
    )
    try:
        assert [binding.role for binding in saved.bindings] == ["front", "multiview"]
        # 顺序即待办顺序：先补缺的用途，再补范围
        assert saved.gaps == ["缺少产品细节图", "产品替换范围"]
        assert saved.revision == 1 and saved.status == "draft"
    finally:
        await drop_pack(saved.id)


async def test_legacy_pack_defaults_to_base_role() -> None:
    """旧包只有 reference_ids：读出来每张都得落在基准用途上，否则一发布就缺图。"""
    pack = await add_pack(reference_ids=["a", "b", "c"], roles=[], status="draft")
    try:
        async with _service() as session:
            shaped = await PackService(session).draft(pack.id, ADMIN)
        assert [binding.role for binding in shaped.bindings] == ["front", "front", "front"]
        assert shaped.required_roles == ["front"]
    finally:
        await drop_pack(pack.id)


@pytest.mark.parametrize(
    ("kind", "role", "message"),
    [
        ("character", "multiview", "图片用途无效"),
        ("scene", "front", "图片用途无效"),
    ],
)
async def test_save_rejects_role_from_another_kind(
    client: TestClient, kind: str, role: str, message: str
) -> None:
    """把产品的「多视结构图」勾到人物包上：不能静默改成正面图让人以为设置生效了。"""
    owner = actor_id(client)
    ids = await _register(client, kind, 1)
    with pytest.raises(BadRequest, match=message):
        await _save(_payload(kind, ids, bindings=[{"role": role}]), owner)


async def test_save_rejects_role_count_mismatch(client: TestClient) -> None:
    owner = actor_id(client)
    ids = await _register(client, "character", 2)
    with pytest.raises(BadRequest, match="一一对应"):
        await _save(_payload("character", ids, bindings=[{"role": "front"}]), owner)


async def test_save_rejects_scope_on_non_product(client: TestClient) -> None:
    """替换范围只对产品有意义：允许人物包带范围会让发布校验永远通过。"""
    owner = actor_id(client)
    ids = await _register(client, "character", 1)
    with pytest.raises(BadRequest, match="只有产品替换需要设置替换范围"):
        await _save(_payload("character", ids, supportedScopes=["worn"]), owner)


async def test_save_rejects_foreign_reference(client: TestClient) -> None:
    """参考图必须是本人的：拿别人的素材 id 拼包等于绕过素材权限。"""
    ids = await _register(client, "character", 1)
    with pytest.raises(BadRequest, match="只能封装可读的已采纳参考图片"):
        await _save(_payload("character", ids), "someone-else")


# --------------------------------------------------------------------------- #
# 发布
# --------------------------------------------------------------------------- #
async def test_publish_requires_required_roles(client: TestClient) -> None:
    owner = actor_id(client)
    ids = await _register(client, "product", 2)
    saved = await _save(
        _payload(
            "product",
            ids,
            bindings=[{"role": "front"}, {"role": "detail"}],
            requiredRoles=["multiview"],
            supportedScopes=["worn"],
        ),
        owner,
    )
    try:
        with pytest.raises(BadRequest) as failure:
            await _publish(saved.id, saved.revision, owner)
        assert "多视结构图" in str(failure.value)
    finally:
        await drop_pack(saved.id)


async def test_publish_requires_product_scope(client: TestClient) -> None:
    owner = actor_id(client)
    ids = await _register(client, "product", 1)
    saved = await _save(_payload("product", ids), owner)
    try:
        with pytest.raises(BadRequest, match="产品替换范围"):
            await _publish(saved.id, saved.revision, owner)
    finally:
        await drop_pack(saved.id)


async def test_publish_complete_pack_is_listed_and_previewable(client: TestClient) -> None:
    owner = actor_id(client)
    ids = await _register(client, "product", 3)
    saved = await _save(
        _payload(
            "product",
            ids,
            bindings=[
                {"role": "front"},
                {"role": "multiview"},
                {"role": "detail", "description": "只补扣件与缝线"},
            ],
            requiredRoles=["multiview", "detail"],
            supportedScopes=["worn", "held"],
        ),
        owner,
    )
    try:
        assert saved.gaps == []
        published = await _publish(saved.id, saved.revision, owner)
        assert published.status == "published" and published.version == 1

        listed = [pack for pack in await _available() if pack["id"] == saved.id]
        assert listed, "已发布的包必须出现在可选列表里"
        pack = listed[0]
        assert pack["available"] is True
        assert pack["cover_url"].endswith(f"/{saved.id}/preview/{ids[0]}?version=1")
        # 封面走公共预览：不打开原件下载端点，字节出口只有 preview 一个
        preview = client.get(pack["cover_url"])
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("image/")
    finally:
        await drop_pack(saved.id)


async def test_disabled_pack_disappears_from_available(client: TestClient) -> None:
    owner = actor_id(client)
    ids = await _register(client, "character", 1)
    saved = await _save(_payload("character", ids), owner)
    try:
        published = await _publish(saved.id, saved.revision, owner)
        async with _service() as session:
            disabled = await PackService(session).disable(saved.id, owner, published.revision)
        assert disabled.status == "disabled"
        assert [pack for pack in await _available() if pack["id"] == saved.id] == []
    finally:
        await drop_pack(saved.id)


async def test_disable_is_idempotent_for_same_revision(client: TestClient) -> None:
    """停用后前端重发同一请求：应返回成功，而不是「状态已变化」。"""
    owner = actor_id(client)
    ids = await _register(client, "character", 1)
    saved = await _save(_payload("character", ids), owner)
    try:
        published = await _publish(saved.id, saved.revision, owner)
        async with _service() as session:
            await PackService(session).disable(saved.id, owner, published.revision)
        async with _service() as session:
            again = await PackService(session).disable(saved.id, owner, published.revision)
        assert again.status == "disabled"
    finally:
        await drop_pack(saved.id)


# --------------------------------------------------------------------------- #
# 身份与幂等
# --------------------------------------------------------------------------- #
async def test_edit_draft_uses_same_identity(client: TestClient) -> None:
    """`requestId` 就是封装 id：编辑不该凭空长出新包。"""
    owner = actor_id(client)
    ids = await _register(client, "character", 1)
    saved = await _save(_payload("character", ids), owner)
    try:
        edited = await _save(
            _payload(
                "character",
                ids,
                requestId=UUID(saved.id),
                expectedRevision=saved.revision,
                name="改名后",
            ),
            owner,
        )
        assert edited.id == saved.id
        assert edited.name == "改名后"
        assert edited.revision == saved.revision + 1
    finally:
        await drop_pack(saved.id)


async def test_repeat_save_with_same_revision_is_idempotent(client: TestClient) -> None:
    """同一次提交因超时重发：返回同一份草稿，而不是「草稿已变化」。

    判据是「内容与 revision 同时命中」——只看 revision 会误判，只看内容会漏掉
    「别人真的改过」。
    """
    owner = actor_id(client)
    ids = await _register(client, "product", 2)
    payload = _payload(
        "product",
        ids,
        bindings=[
            {"role": "front", "description": "主图"},
            {"role": "multiview", "description": "多视"},
        ],
    )
    first = await _save(payload, owner)
    try:
        again = await _save(payload, owner)
        assert again.id == first.id
        assert again.revision == first.revision
        # 内容变了但 revision 落后：这才是真的冲突
        with pytest.raises(Conflict, match="草稿已变化"):
            await _save(payload.model_copy(update={"name": "改过的名字"}), owner)
    finally:
        await drop_pack(first.id)


async def test_save_against_missing_draft_conflicts() -> None:
    """带 `expectedRevision` 但草稿不存在：说明前端看的那份已经没了。"""
    with pytest.raises(Conflict, match="草稿不存在"):
        await _save(
            PackDraftIn.model_validate(
                {
                    "requestId": uuid.uuid4(),
                    "expectedRevision": 3,
                    "kind": "character",
                    "name": "幽灵草稿",
                }
            )
        )


async def test_other_owner_sees_not_found(client: TestClient) -> None:
    """不泄露「存在但不是你的」：404 而不是 403。"""
    ids = await _register(client, "character", 1)
    saved = await _save(_payload("character", ids))
    try:
        async with _service() as session:
            with pytest.raises(NotFound):
                await PackService(session).draft(saved.id, "u_someone_else")
    finally:
        await drop_pack(saved.id)


# --------------------------------------------------------------------------- #
# 封面
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cover", "expected"),
    [
        (None, "a"),
        ("c", "c"),
        # 指向包外（素材换过/包删了那张图）：退回第一张，而不是把死 id 写进出参
        ("gone", "a"),
    ],
)
async def test_cover_resolves_inside_pack(cover: str | None, expected: str) -> None:
    pack = await add_pack(
        reference_ids=["a", "b", "c"],
        roles=["multiview", "front", "detail"],
        status="draft",
    )
    try:
        async with _service() as session:
            repository = PackRepository(session)
            row = await repository.get_or_404(pack.id)
            row.cover_asset_id = cover
            await repository.save(row)
            await session.commit()
            shaped = await PackService(session).draft(pack.id, ADMIN)
        assert shaped.reference_ids == ["a", "b", "c"]
        assert shaped.cover_reference_id == expected
    finally:
        await drop_pack(pack.id)


# --------------------------------------------------------------------------- #
# 词表（前后端共用）
# --------------------------------------------------------------------------- #
def test_every_kind_has_base_role_and_usage_text() -> None:
    """用途表、基准角色、提示词说明三张表必须对得上。

    漏一条的症状是「编辑器里能选，生成时那段说明是空的」——提示词少一句约束，
    出来的片子人物不像、产品换了颜色，而日志里没有任何异常。
    """
    from modules.template.pack_roles import BASE_ROLE, ROLE_USAGE, usage_for

    for kind, roles in PACK_ROLES.items():
        assert BASE_ROLE[kind] in roles, f"{kind} 的基准用途不在自己的用途表里"
        assert set(ROLE_USAGE[kind]) == set(roles), f"{kind} 的说明表与用途表不一致"
        for role in roles:
            assert usage_for(kind, role).strip(), f"{kind}.{role} 没有提示词说明"


def test_scope_table_is_shared_with_frontend_shape() -> None:
    """范围词表要能整体下发给前端：`/replacement-catalog` 里三张表都在。"""
    assert set(PACK_SCOPES) == {"worn", "held", "boxed", "screen"}
