"""`assets` 表的约束与默认值。

这里验的是**数据库层**的保证，不是业务逻辑：去重由归档服务先查后写，
但那套逻辑只有在「数据库真的会拒绝第二条重复摘要」时才成立。少了这条约束，
并发上传同一文件会静默产生两条记录和两份文件。

索引口径必须与 `ArchiveService._find_by_digest` 完全一致，否则会出现两种失效：
索引比代码宽 → 并发重复无人拦；索引比代码窄 → 正常用法被数据库拒掉，
而客户端收到的是 500 而不是可读冲突。所以下面既验「该拒的拒」，
也验「不该拒的不拒」——后者是这次修 `source != 'archive'` 那个死条件时
最容易踩回去的地方。

**归属隔离不在这里**：它以前有一条对着 `AssetService` 的单元用例，两套素材接口
合并时那个服务被删了。现在隔离由归档服务的 `owned()` 一处保证，端到端覆盖在
`test_assets_integration.py`（别人改不了你的分享开关）与 `test_replacement_review.py`
（别人的任务一律 404）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db.session import get_session_factory
from modules.asset.models import Asset

_OWNER = "asset-model-test-owner"
_OTHER = "asset-model-test-owner-2"


async def _clear() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(Asset).where(Asset.owner_id.in_([_OWNER, _OTHER])))
        await session.commit()


@pytest.fixture
async def session():
    await _clear()
    factory = get_session_factory()
    async with factory() as s:
        yield s
    await _clear()


async def _add(
    session,
    *,
    sha256: str | None,
    name: str,
    category: str | None = "source",
    deleted: bool = False,
) -> Asset:
    asset = Asset(
        name=name,
        type="video" if category in (None, "source") else "image",
        owner_id=_OWNER,
        sha256=sha256,
        category=category,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(asset)
    await session.flush()
    return asset


async def test_defaults_match_ingest_semantics(session) -> None:
    """默认来源是 `archive`。

    接口合并后网页上传也走 `_ingest`，字节一律进归档存储；`upload` 只剩早期数据。
    默认值留在 `upload` 会让「新写入的行」一落地就不属于任何一次真实入口。
    """
    asset = await _add(session, sha256=None, name="默认值.png", category=None)

    assert asset.source == "archive", "所有写入都记 archive"
    assert asset.category is None, "未分类就该是 NULL，不是空串"
    assert asset.cover_key == ""
    assert asset.duration == 0.0
    assert asset.has_audio is False


async def test_null_digest_rows_do_not_collide(session) -> None:
    """历史归档行与算不出摘要的行都要能共存。"""
    await _add(session, sha256=None, name="a.png")
    await _add(session, sha256=None, name="b.png")

    rows = (
        await session.scalars(select(Asset).where(Asset.owner_id == _OWNER))
    ).all()
    assert len(rows) == 2


async def test_same_owner_same_digest_same_category_is_rejected(session) -> None:
    """同一份文件在同一分类下只能有一条：这是并发上传的最后一道兜底。"""
    await _add(session, sha256="a" * 64, name="first.mp4")

    with pytest.raises(IntegrityError):
        await _add(session, sha256="a" * 64, name="second.mp4")

    await session.rollback()


async def test_same_digest_in_another_category_is_allowed(session) -> None:
    """同一张图同时当人物参考与产品参考是正常用法，不能被判成重复。

    旧索引没有 `category` 列，一旦把 `source != 'archive'` 那个条件改掉，
    这种用法会被数据库直接拒掉——所以这条和上一条必须成对存在。
    """
    await _add(session, sha256="c" * 64, name="人物.png", category="character")
    await _add(session, sha256="c" * 64, name="产品.png", category="product")

    rows = (
        await session.scalars(select(Asset).where(Asset.owner_id == _OWNER))
    ).all()
    assert len(rows) == 2


async def test_trashed_row_does_not_block_a_fresh_upload(session) -> None:
    """回收站里的旧记录不占唯一键，否则「删掉再传同一个文件」永远失败。

    这正是把条件从 `source != 'archive'` 换成 `deleted_at IS NULL` 的目的：
    前者对所有行都不成立（索引形同不存在），后者才是业务真正要的语义。
    """
    await _add(session, sha256="d" * 64, name="旧.mp4", deleted=True)
    await _add(session, sha256="d" * 64, name="新.mp4")

    rows = (
        await session.scalars(select(Asset).where(Asset.owner_id == _OWNER))
    ).all()
    assert len(rows) == 2


async def test_same_digest_other_owner_is_allowed(session) -> None:
    """去重范围是「个人素材库内」：两个人各传同一份文件，各有一份记录。"""
    await _add(session, sha256="b" * 64, name="mine.mp4")
    other = Asset(
        name="theirs.mp4", type="video", owner_id=_OTHER, sha256="b" * 64, category="source"
    )
    session.add(other)
    await session.flush()

    assert other.id != ""


def test_dedupe_index_matches_the_service_lookup() -> None:
    """索引口径必须与 `_find_by_digest` 的 WHERE 一一对应。

    这条守的是「改一处忘另一处」：索引比查询宽松，并发就漏；比查询严格，
    正常操作就撞库。两者都不报错，只在特定时序或特定用法下才暴露。

    不看 SQL 文本（方言差异大），只看**结构化事实**：索引列 == 查询列。
    """
    index = next(
        idx
        for idx in Asset.__table__.indexes
        if idx.name == "uq_assets_owner_sha256"
    )
    assert index.unique, "去重索引必须唯一，否则等于没建"
    assert [column.name for column in index.columns] == [
        "owner_id",
        "sha256",
        "category",
    ], "列必须与 _find_by_digest 的筛选条件同集合：owner + 摘要 + 分类"

    where = str(index.dialect_options["sqlite"]["where"])
    assert "deleted_at IS NULL" in where, (
        "必须是「只看未删除行」的部分索引：回收站旧记录不该挡住重新上传"
    )
    assert "source" not in where, (
        "不能再用 source 做条件：所有写入都是 archive，那样索引一行都进不去"
    )


async def test_index_is_actually_created_on_the_live_database() -> None:
    """模型里声明了不等于库里有：迁移漏了也能全绿。

    走 `run_sync` 而不是直接 `inspect(get_engine())`：AsyncEngine 上的属性访问
    会触发 IO，同步 inspect 拿到的是 `MissingGreenlet`，而不是「索引不存在」。
    """
    from sqlalchemy import inspect as sa_inspect

    from db.session import get_session_factory

    async with get_session_factory()() as session:
        connection = await session.connection()
        names = await connection.run_sync(
            lambda sync_connection: {
                index["name"] for index in sa_inspect(sync_connection).get_indexes("assets")
            }
        )
    assert "uq_assets_owner_sha256" in names
