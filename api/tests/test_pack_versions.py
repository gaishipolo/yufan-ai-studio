"""发布版号的 CAS：`UNIQUE(pack_id, version)` 才是仲裁者。

重建前这里守的是「归档存储上的 `create()`（`If-None-Match: *`）有没有抢到版号」；
换成数据库之后**语义不变、机制换了**：

| 原来 | 现在 |
|---|---|
| `storage.create(<版本文件>)` 返回 False = 被抢 | 插入撞 `UNIQUE(pack_id, version)` = 被抢 |
| 抢不到就试下一个版号 | 一样试下一个版号 |
| 崩溃可能留下截断的版本文件（那个版号永久黑洞） | 事务回滚，什么都不留 |

所以这个文件保留了下来，只是断言对象从「文件存不存在」换成「行有没有多出来」。
两条判据一条都不能松：**已发布版本不可变**、**重试不能把调用方的事务一起回滚**。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import ADMIN, add_pack, drop_pack, upload_ids

from core.exceptions import Conflict
from db.session import get_session_factory
from modules.template_pack.models import (
    TemplatePack,
    TemplatePackVersion,
)
from modules.template_pack.repository import PackRepository


def _build(pack_id: str, name: str):
    """版本行构造器：`create_version` 每轮重试都要一份新的待插入对象。"""

    def build(version: int) -> TemplatePackVersion:
        return TemplatePackVersion(
            pack_id=pack_id,
            version=version,
            reference_ids=["ref"],
            bindings=[{"role": "front", "description": ""}],
            required_roles=["front"],
            supported_scopes=[],
            features="",
            instructions=name,
            wardrobe_mode="preserve",
            published_at=datetime.now(UTC),
        )

    return build


async def test_taken_version_is_skipped_not_overwritten() -> None:
    """两个实例都从 1 开始抢：第二个必须顺延，而不是覆盖第一个。

    覆盖的后果不是「少一版」，而是**两份内容不同却同版本号**——已发布版本按定义
    不可变，覆盖等于篡改历史。
    """
    pack = await add_pack(status="draft")
    try:
        async with get_session_factory()() as session:
            repository = PackRepository(session)
            first = await repository.create_version(pack.id, 1, _build(pack.id, "实例 A 的内容"))

        async with get_session_factory()() as session:
            repository = PackRepository(session)
            second = await repository.create_version(pack.id, 1, _build(pack.id, "实例 B 的内容"))
            stored = await repository.get_version(pack.id, first)

        assert (first, second) == (1, 2)
        assert stored is not None
        assert stored.instructions == "实例 A 的内容", "已发布版本被后来者覆盖了"
    finally:
        await drop_pack(pack.id)


async def test_conflict_retry_keeps_the_callers_pending_writes() -> None:
    """撞版号时只能回滚**这一次插入**。

    `create_version` 第一版用 `session.rollback()`，于是同一事务里已经写好的
    封装 head（版本号、状态）一起被回滚——用户看到「发布失败」，回头发现草稿
    内容也回到了上一次保存的状态。改成 savepoint 之后这两个失败才分得开。
    """
    pack = await add_pack(status="draft")
    try:
        async with get_session_factory()() as session:
            await PackRepository(session).create_version(pack.id, 1, _build(pack.id, "占住 1"))

        async with get_session_factory()() as session:
            repository = PackRepository(session)
            row = await repository.get_or_404(pack.id)
            row.version = 7
            row.status = "published"
            await repository.save(row)

            claimed = await repository.create_version(pack.id, 1, _build(pack.id, "撞车后顺延"))
            await session.commit()

        assert claimed == 2
        fresh = get_session_factory()
        async with fresh() as session:
            stored = await session.get(TemplatePack, pack.id)
            assert stored is not None
            assert (stored.version, stored.status) == (7, "published"), (
                "重试把调用方同一事务里已写好的封装 head 一起回滚了"
            )
    finally:
        await drop_pack(pack.id)


async def test_repeated_conflicts_eventually_give_up() -> None:
    """连着被抢时不能无限自增：让调用方刷新重来，而不是占掉一堆版号。"""
    pack = await add_pack(status="draft")
    try:
        async with get_session_factory()() as session:
            repository = PackRepository(session)
            for version in range(1, 6):
                await repository.create_version(pack.id, version, _build(pack.id, "占位"))

        async with get_session_factory()() as session:
            with pytest.raises(Conflict, match="发布冲突过于频繁"):
                await PackRepository(session).create_version(pack.id, 1, _build(pack.id, "抢不到"))
    finally:
        await drop_pack(pack.id)


async def test_version_number_and_revision_are_different_things() -> None:
    """`version` 是「发布了几版」，`revision` 是「草稿改了几次」。

    两者混成一个字段的症状：改一次草稿（不发）界面就显示「已发布 v3」，而历史
    版本列表里其实只有 v1。
    """
    pack = await add_pack(status="draft")
    try:
        async with get_session_factory()() as session:
            repository = PackRepository(session)
            row = await repository.get_or_404(pack.id)
            # 改三次草稿，一次都不发布
            for _ in range(3):
                row.revision += 1
            await repository.save(row)
            await session.commit()

            assert await repository.list_versions(pack.id) == []
        async with get_session_factory()() as session:
            row = await PackRepository(session).get_or_404(pack.id)
        assert (row.revision, row.version) == (4, 0)
    finally:
        await drop_pack(pack.id)


async def test_version_rows_are_written_in_order() -> None:
    """历史版本按版号倒序返回：前端「上次发布的版本」取第一条。"""
    pack = await add_pack(status="draft")
    try:
        async with get_session_factory()() as session:
            repository = PackRepository(session)
            for start in (1, 2, 3):
                await repository.create_version(pack.id, start, _build(pack.id, f"第 {start} 版"))
            versions = await repository.list_versions(pack.id)
        assert [row.version for row in versions] == [3, 2, 1]
        assert versions[0].instructions == "第 3 版"
    finally:
        await drop_pack(pack.id)


async def test_publishing_same_revision_twice_is_idempotent(client: TestClient) -> None:
    """发布成功后前端重发（超时、双击）：返回成功，不能再长出一版。

    判据是「已发布 + 版号 > 0 + revision 相同」，不是「revision 相同」——后者
    会让一次真正的重发冒出一个空版本。
    """
    from modules.template_pack.pack_service import PackService

    ids = upload_ids(client, "character", 1)
    async with get_session_factory()() as session:
        service = PackService(session)
        draft = await service.save(_draft(ids), ADMIN)
    try:
        async with get_session_factory()() as session:
            first = await PackService(session).publish(draft.id, ADMIN, draft.revision)
        async with get_session_factory()() as session:
            again = await PackService(session).publish(draft.id, ADMIN, draft.revision)
        assert (first.version, again.version) == (1, 1)
        async with get_session_factory()() as session:
            versions = await PackRepository(session).list_versions(draft.id)
        assert len(versions) == 1, "重发发布不该多出一个空版本"
    finally:
        await drop_pack(draft.id)


async def test_publish_time_comes_from_the_shared_clock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发布时间要能钉住：它得取自 `db.base.utcnow`，而不是各处自己 `datetime.now`。

    时间源各自为政时，想验「发布时间落在这条版本上」只能等、或者放开容差——
    而这一列是判断「哪一版先发」的唯一依据。
    """
    from modules.template_pack import pack_service

    pinned = datetime(2026, 11, 13, 4, 30, tzinfo=UTC)
    ids = upload_ids(client, "character", 1)
    async with get_session_factory()() as session:
        draft = await pack_service.PackService(session).save(_draft(ids), ADMIN)
    try:
        monkeypatch.setattr(pack_service, "utcnow", lambda: pinned)
        async with get_session_factory()() as session:
            await pack_service.PackService(session).publish(draft.id, ADMIN, draft.revision)
        async with get_session_factory()() as session:
            versions = await PackRepository(session).list_versions(draft.id)
            row = await session.get(TemplatePack, draft.id)
        # 版本行与封装行两处都取自同一个钟：漏一处就会「包说 A 时发的、版本说 B 时」。
        # sqlite 不带时区，取回来的是 naive，比较前补上 UTC（值本身没变）。
        assert [version.published_at.replace(tzinfo=UTC) for version in versions] == [pinned]
        assert row is not None and row.published_at is not None
        assert row.published_at.replace(tzinfo=UTC) == pinned
    finally:
        await drop_pack(draft.id)


def _draft(ids: list[str], **extra):
    import uuid

    from modules.template_pack.schema import PackDraftIn

    values = {
        "requestId": uuid.uuid4(),
        "kind": "character",
        "name": "幂等发布",
        "referenceLibraryIds": ids,
        "coverLibraryId": ids[0] if ids else None,
    }
    values.update(extra)
    return PackDraftIn.model_validate(values)


async def test_same_revision_with_new_content_still_mints_a_version(client: TestClient) -> None:
    """幂等判定不能宽到「同一 revision 就算发过了」。

    漏了这条，补一张参考图后再点发布会被当成重发：界面显示「发布成功」，
    实际对外可见的版本还是旧的，而且没有任何报错能看出来。
    """
    from modules.template_pack.pack_service import PackService

    ids = upload_ids(client, "character", 2)
    async with get_session_factory()() as session:
        draft = await PackService(session).save(_draft([ids[0]]), ADMIN)
    try:
        async with get_session_factory()() as session:
            await PackService(session).publish(draft.id, ADMIN, draft.revision)

        async with get_session_factory()() as session:
            edited = await PackService(session).save(
                _draft([ids[0], ids[1]], requestId=draft.id, expectedRevision=draft.revision),
                ADMIN,
            )
        assert edited.revision == draft.revision + 1

        async with get_session_factory()() as session:
            second = await PackService(session).publish(draft.id, ADMIN, edited.revision)
            versions = await PackRepository(session).list_versions(draft.id)

        assert second.version == 2
        assert [row.version for row in versions] == [2, 1]
        assert versions[-1].reference_ids == [ids[0]], "旧版本被后一次发布改写了"
        assert versions[0].reference_ids == [ids[0], ids[1]]
    finally:
        await drop_pack(draft.id)
