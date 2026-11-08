"""镜头预设的演示数据：运镜方式 + 提示词片段。

`popularity` 拉开梯度（510 ~ 980），因为预设列表是按它排的——全填同一个值，
"排序是不是真的生效"在开发时看不出来。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.demo import minutes_ago, placeholder_image
from modules.shot.models import ShotPreset

# (名称, 提示词片段, 时长, 热度)
_SHOTS: list[tuple[str, str, int, int]] = [
    ("推镜", "缓慢向前推进，主体逐渐充满画面", 3, 980),
    ("拉镜", "镜头后退，揭示环境全貌", 3, 720),
    ("摇镜", "水平摇摄，扫过场景", 4, 640),
    ("环绕", "围绕主体 180 度环绕", 5, 860),
    ("升降", "从地面升到高空俯瞰", 4, 510),
    ("手持跟拍", "手持轻微晃动跟随主体移动", 3, 770),
]


async def seed(session: AsyncSession, context: SeedContext) -> int:
    rows = [
        ShotPreset(
            name=name,
            prompt_fragment=fragment,
            duration_sec=duration,
            cover_url=placeholder_image(f"sh{i}", 480, 270),
            popularity=popularity,
            created_at=minutes_ago(i * 900 + 300),
        )
        for i, (name, fragment, duration, popularity) in enumerate(_SHOTS)
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)
