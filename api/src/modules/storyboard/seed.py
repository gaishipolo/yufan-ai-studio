"""分镜模块的演示数据：三条，画幅各不相同（竖屏 / 横屏 / 方形）。

画幅混着种是有意的：分镜列表的封面裁切、以及"按画幅筛选"这条路径，
只有三种比例都出现时才会暴露布局问题。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.demo import minutes_ago, placeholder_image
from core.enums import AspectRatio
from modules.storyboard.models import Storyboard

# (名称, 说明, 镜头数, 时长, 画幅)
_STORYBOARDS: list[tuple[str, str, int, int, AspectRatio]] = [
    ("15 秒快节奏产品片", "三个镜头讲完卖点，适合信息流投放", 3, 15, AspectRatio.PORTRAIT),
    ("30 秒品牌故事", "铺垫情绪，慢节奏运镜，适合品牌号", 6, 30, AspectRatio.LANDSCAPE),
    ("9 宫格产品展示", "方形构图，适合小红书与店铺详情", 9, 18, AspectRatio.SQUARE),
]


async def seed(session: AsyncSession, context: SeedContext) -> int:
    rows = [
        Storyboard(
            name=name,
            description=desc,
            shot_count=shots,
            duration_sec=duration,
            aspect_ratio=ratio.value,
            cover_url=placeholder_image(f"sb{i}", 640, 360),
            created_at=minutes_ago(i * 800 + 200),
        )
        for i, (name, desc, shots, duration, ratio) in enumerate(_STORYBOARDS)
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)
