"""素材模块的演示数据。

三种类型都种（图片 / 视频 / 音频），且 `ref_count` 有 0 有正值：素材列表的
「被引用」筛选、以及回收站「仍被引用不能删」这条规则，靠 0 与正值并存才看得到。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.demo import minutes_ago, placeholder_image
from modules.asset.models import Asset

# (名称, 类型, 扩展名, 宽, 高, 引用数)
_ASSETS: list[tuple[str, str, str, int, int, int]] = [
    ("冰川场景参考图", "image", "png", 2400, 3200, 4),
    ("霓虹街道素材", "video", "mp4", 1920, 1080, 2),
    ("品牌片头音效", "audio", "wav", 0, 0, 1),
    ("产品白底图", "image", "jpg", 2000, 2000, 6),
    ("人物参考半身像", "image", "png", 1600, 2400, 3),
    ("城市航拍片段", "video", "mp4", 3840, 2160, 0),
    ("转场音效包", "audio", "mp3", 0, 0, 2),
    ("材质贴图-金属", "image", "png", 1024, 1024, 5),
    ("片尾字幕背景", "video", "mp4", 1920, 1080, 1),
]


async def seed(session: AsyncSession, context: SeedContext) -> int:
    rows = [
        Asset(
            name=name,
            type=kind,
            mime_type=f"{kind}/{ext}",
            size_bytes=(1200 + i * 340) * 1024,
            cover_url=placeholder_image(f"as{i}", 600, 600),
            width=w,
            height=h,
            ref_count=refs,
            owner_id=context.demo_user_id,
            created_at=minutes_ago(i * 120 + 30),
        )
        for i, (name, kind, ext, w, h, refs) in enumerate(_ASSETS)
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)
