"""模板模块的演示数据：四条覆盖电商 / 本地生活 / 知识 / 文旅。

`usage_count` 刻意拉开量级（74 ~ 210）：模板列表按使用量排序时，第一屏是不是
「明显该排在前面的那几个」能一眼判断出来，全填同一个数就看不出来了。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.demo import minutes_ago, placeholder_image
from modules.template.models import VideoTemplate

# (名称, 说明, 行业, 镜头数, 使用次数, 标签)
_TEMPLATES: list[tuple[str, str, str, int, int, str]] = [
    ("电商产品三段式", "痛点开场 → 产品特写 → 场景使用", "电商", 3, 128, "618,新品,转化"),
    ("本地生活探店", "门头 → 招牌菜 → 顾客反应 → 团购引导", "本地生活", 4, 96, "探店,餐饮,团购"),
    ("知识口播模板", "钩子 → 干货三条 → 引导关注", "知识付费", 3, 210, "口播,涨粉"),
    ("旅拍氛围片", "空镜 → 人物入镜 → 情绪特写 → 落日收尾", "文旅", 4, 74, "旅拍,氛围"),
]


async def seed(session: AsyncSession, context: SeedContext) -> int:
    rows = [
        VideoTemplate(
            name=name,
            description=desc,
            industry=industry,
            shot_count=shots,
            usage_count=usage,
            cover_url=placeholder_image(f"tp{i}", 640, 360),
            tags=tags,
            created_at=minutes_ago(i * 600 + 120),
        )
        for i, (name, desc, industry, shots, usage, tags) in enumerate(_TEMPLATES)
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)
