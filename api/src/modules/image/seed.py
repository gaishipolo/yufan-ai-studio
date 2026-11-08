"""生图模块的演示数据：`app seed` 时由 `ModuleSpec.seed` 调用（手册 §5.4 ①）。

## 为什么放在模块里

这些提示词、尺寸、"演示用"判断都是**生图这个功能自己的事**。以前它们写在
`src/seed.py` 里，于是那个文件同时知道 users / tasks / assets / templates /
storyboards / shots / images 七种模型——每加一个模块就要再去改它一次，
新模块的演示数据写着写着就变成了框架债。

## 状态为什么是 `succeeded` 而不是默认的 `queued`

默认值 `queued` 会让演示数据看起来像「永远在排队」；更要紧的是，
`modules/image/maintenance.py` 的巡检会把「queued 且超过 90 秒」的作业**重新投递**，
而演示数据的时间戳都是几十分钟前——一旦在真实环境跑了 `app seed`，
巡检就会拿演示记录去调真实供应商。**演示数据必须是终态。**
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from db.base import utcnow
from modules.image.models import ImageJob

DEMO_OWNER = "u_001"

_PROMPTS: list[tuple[str, str]] = [
    ("赛博朋克少女", "赛博朋克风格少女半身像，霓虹光，胶片质感"),
    ("极简产品图", "白色背景上的香水瓶，柔光棚拍"),
    ("水彩风景", "水彩风格的山间小屋，晨雾"),
    ("国潮插画", "国潮风格龙纹插画，朱红与金"),
]


def _ago(minutes: int) -> datetime:
    return utcnow() - timedelta(minutes=minutes)


async def seed(session: AsyncSession) -> int:
    rows = [
        ImageJob(
            name=title,
            prompt=prompt,
            size="1024x1536",
            status="succeeded",
            progress=100,
            model="Agnes Image 2.5 Flash",
            model_id="agnes-image-2.5-flash",
            cover_url=f"https://picsum.photos/seed/img{i}/400/500",
            output_url=f"https://picsum.photos/seed/img{i}/1024/1536",
            owner_id=DEMO_OWNER,
            created_at=_ago(i * 240 + 60),
            finished_at=_ago(i * 240 + 58),
        )
        for i, (title, prompt) in enumerate(_PROMPTS)
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)
