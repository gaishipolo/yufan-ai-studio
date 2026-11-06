"""`assets` 表的约束与默认值。

这里验的是**数据库层**的保证，不是业务逻辑：去重由 `AssetService` 先查后写，
但那套逻辑只有在「数据库真的会拒绝第二条重复摘要」时才成立。少了这条约束，
并发上传同一文件会静默产生两条记录和两份文件。
"""

from __future__ import annotations

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


async def _add(session, *, sha256: str | None, name: str) -> Asset:
    asset = Asset(name=name, type="image", owner_id=_OWNER, sha256=sha256)
    session.add(asset)
    await session.flush()
    return asset


async def test_defaults_match_upload_semantics(session) -> None:
    asset = await _add(session, sha256=None, name="默认值.png")

    assert asset.source == "upload", "网页上传是默认来源"
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


async def test_same_owner_same_digest_is_rejected(session) -> None:
    await _add(session, sha256="a" * 64, name="first.png")

    with pytest.raises(IntegrityError):
        await _add(session, sha256="a" * 64, name="second.png")

    await session.rollback()


async def test_same_digest_other_owner_is_allowed(session) -> None:
    """去重范围是「个人素材库内」：两个人各传同一份文件，各有一份记录。"""
    await _add(session, sha256="b" * 64, name="mine.png")
    other = Asset(name="theirs.png", type="image", owner_id=_OTHER, sha256="b" * 64)
    session.add(other)
    await session.flush()

    assert other.id != ""


async def test_service_hides_other_owners_asset(session) -> None:
    """归属隔离在 service 层就断掉：别人的素材一律当作不存在。"""
    from core.context import CurrentUser
    from infra.storage.local import LocalStorage
    from modules.asset.errors import AssetNotFound
    from modules.asset.service import AssetService

    foreign = Asset(name="foreign.png", type="image", owner_id=_OTHER)
    session.add(foreign)
    await session.flush()

    service = AssetService(session, LocalStorage())
    actor = CurrentUser(id=_OWNER, name="tester")

    with pytest.raises(AssetNotFound):
        await service.get_asset(actor, foreign.id)
    with pytest.raises(AssetNotFound):
        await service.delete_asset(actor, foreign.id)
