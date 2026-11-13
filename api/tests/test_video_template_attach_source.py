"""换样片原片：文件名、时长与生成档位都要跟着资产走。

只写 `assetId` 的后果是「卡片换了、说明没换」：封装管理里仍是上一支片的名字和
秒数，生成档位也还按旧时长取整。但**手调过的档位不能覆盖**——那是人做的决定，
不是时长算出来的，所以只在「档位 == 旧时长取整」时才跟着改。
"""

from __future__ import annotations

import pytest
from video_template_support import put, record, run

from core.exceptions import NotFound
from db.session import get_session_factory
from modules.asset.models import Asset
from modules.template import video_template_service as service

NEW_ASSET = "ffeeb073241c5e7d76bf46fc8544e887fe0da464"


def source_asset(asset_id: str, name: str, duration: float) -> None:
    """造一条 v1 `source` 原片：`attach_source` 只认这一类。

    **同 id 覆盖**而不是「存在就跳过」：测试库整场共用，前面的用例可能已经用
    同一个 id 造过一条时长不同的原片，跳过会让这条用例拿到别人的 6 秒。
    """

    async def go() -> None:
        async with get_session_factory()() as session:
            row = await session.get(Asset, asset_id)
            if row is None:
                session.add(
                    Asset(
                        id=asset_id,
                        name=name,
                        type="video",
                        category="source",
                        owner_id="u_001",
                        duration=duration,
                    )
                )
            else:
                row.duration = duration
                row.name = name
            await session.commit()

    run(go())


def attach(template_id: str, asset_id: str):
    async def go():
        async with get_session_factory()() as session:
            return await service.attach_source(template_id, asset_id, session)

    return run(go())


def test_rebinding_follows_the_new_asset_name_and_duration() -> None:
    # 样片 5.312 秒 → 创建时档位取整成 5
    put(**record(id="attach-1", templateKey="attach-1", pinned=False,
                 generation={"duration": 5, "ratio": "9:16"}))
    source_asset(NEW_ASSET, "我穿的是米兰之夜3030.mp4", 6.0)

    target = attach("attach-1", NEW_ASSET)

    assert target.source_video_asset_id == NEW_ASSET
    assert target.source_video_name == "我穿的是米兰之夜3030.mp4"
    assert target.source_duration_sec == 6.0
    # 5 恰好是旧时长取整，说明它是算出来的，跟着新时长走
    assert target.generation["duration"] == 6
    assert target.generation["ratio"] == "9:16"


def test_hand_tuned_duration_survives_rebinding() -> None:
    put(**record(id="attach-2", templateKey="attach-2", pinned=False,
                 generation={"duration": 8, "ratio": "9:16"}))
    source_asset(NEW_ASSET, "我穿的是米兰之夜3030.mp4", 6.0)

    target = attach("attach-2", NEW_ASSET)
    assert target.source_duration_sec == 6.0
    # 8 不是旧时长取整的结果，是人调过的，不许覆盖
    assert target.generation["duration"] == 8


def test_missing_template_and_missing_duration() -> None:
    put(**record(id="attach-3", templateKey="attach-3", pinned=False,
                 generation={"duration": 5, "ratio": "9:16"}))
    source_asset(NEW_ASSET, "新片.mp4", 0)

    with pytest.raises(NotFound):
        attach("missing", NEW_ASSET)

    # 探测不到时长时只换 id 和名字，不拿 0 覆盖已有档位
    target = attach("attach-3", NEW_ASSET)
    assert target.generation["duration"] == 5
    assert target.source_duration_sec == 5.312
