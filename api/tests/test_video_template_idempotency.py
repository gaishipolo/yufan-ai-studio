"""`templateKey` 的幂等语义：同键同配置返回原行，同键异配置 409。

落表之前这一段靠 `_LOCK` 里的「先扫一遍再追加」保证；现在第一道是唯一索引
`uq_video_templates_template_key`，第二道才是「读到已存在行再比对配置」。
两条都要在：

- 只有比对没有索引 → 两个并发重试都能通过 `get_by_key()` 的检查，最后插两条；
- 只有索引没有比对 → 同键异配置报的是 IntegrityError（500），
  而用户该看到的是「换个稳定标识」。

**`pinned` 不参与比对**：别人抢了蓝本不该让一次重试报冲突——蓝本是独立管理状态。
"""

from __future__ import annotations

import pytest
from library_support import insert_asset
from video_template_support import run

from core.config import settings
from core.exceptions import Conflict
from db.session import get_session_factory
from modules.template_pack import video_template_service as service
from modules.template_pack.video_template_schema import VideoTemplateDraftIn, VideoTemplateMedia

_SOURCE: str | None = None


def source_id() -> str:
    """一条可读的视频原片，整个文件共用一个。

    封装前置条件从「传了 id」变成「资产库里真有这条视频」，所以这里必须先造
    一行。它只是让 `create()` 过闸，本文件的题目是 `templateKey` 的幂等语义，
    所以不打算为每个用例各造一条。
    """
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = insert_asset(
            owner_id=settings.demo_user_id,
            kind="video",
            state="active",
            name="原片.mp4",
            mime_type="video/mp4",
        )
    return _SOURCE


def draft(**overrides: object) -> VideoTemplateDraftIn:
    base: dict[str, object] = {
        "name": "并发重试模板",
        "template_key": "concurrent-retry",
        "source_video_asset_id": source_id(),
        "prompt": "对 {{Mixed 1}} 做替换，使用 {{Mixed 2}}。",
        "media_order": [
            VideoTemplateMedia(
                token="{{Mixed 1}}", role="source_video", type="video", name="样片", in_prompt=True
            ),
            VideoTemplateMedia(
                token="{{Mixed 2}}",
                role="front",
                type="image",
                asset_id="img-a",
                name="主图",
                in_prompt=True,
            ),
        ],
    }
    base.update(overrides)
    return VideoTemplateDraftIn.model_validate(base)


def create(payload: VideoTemplateDraftIn):
    async def go():
        async with get_session_factory()() as session:
            return await service.create(payload, session)

    return run(go())


def test_same_key_and_configuration_returns_original_record() -> None:
    first = create(draft(template_key="retry-key"))
    # 换一个 id 重试：命中的仍应是原行
    retry = create(draft(template_key="retry-key"))

    assert first.id == retry.id
    assert first.template_key == "retry-key"


def test_key_conflict_does_not_append_or_unpin() -> None:
    original = create(draft(template_key="conflict-key", pinned=True))
    with pytest.raises(Conflict, match="稳定标识"):
        create(
            draft(
                template_key="conflict-key",
                prompt="对 {{Mixed 1}} 做替换，使用 {{Mixed 2}}！",
            )
        )
    # 冲突不该把已经落库的那条改掉，也不该多出一行
    assert create(draft(template_key="conflict-key")).id == original.id

    async def read():
        async with get_session_factory()() as session:
            return await service.list_out(session)

    listing = run(read())
    mine = [item for item in listing.items if item.template_key == "conflict-key"]
    assert len(mine) == 1
    assert mine[0].pinned is True


def test_pin_change_does_not_break_original_creation_retry() -> None:
    """重试一次创建，不该因为「中途有人把这条设成蓝本」而报冲突或抢回蓝本。"""
    created = create(draft(template_key="pin-retry-key", pinned=False))

    async def pin_it():
        async with get_session_factory()() as session:
            return await service.pin(created.id, session)

    run(pin_it())
    retried = create(draft(template_key="pin-retry-key", pinned=False))
    assert retried.id == created.id
    assert retried.pinned is True
