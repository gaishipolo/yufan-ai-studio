"""任务模块的演示数据。

这批任务覆盖四种状态（运行中 / 已完成 / 已失败 / 排队中），是为了让列表页的
筛选、进度条、失败态、可下载标记在开发时都能被肉眼看到——只种「已完成」的话，
「失败长什么样」要等到线上第一次出错才知道。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.demo import minutes_ago, placeholder_image
from modules.task.enums import AspectRatio, TaskStatus
from modules.task.models import VideoTask

_TASKS: list[tuple[str, str, TaskStatus, str, int, AspectRatio]] = [
    ("冰川之上的机械鲸鱼", "黎明时分，一头由黄铜与齿轮构成的巨鲸破冰而出", TaskStatus.RUNNING, "Seedance 2.5", 15, AspectRatio.PORTRAIT),
    ("雨夜霓虹街头跟拍", "手持镜头跟随风衣女子穿过雨夜霓虹街道，浅景深", TaskStatus.RUNNING, "Seedance 2.5", 10, AspectRatio.PORTRAIT),
    ("沙漠列车长镜头", "一列蒸汽火车穿越金色沙丘，航拍环绕", TaskStatus.COMPLETED, "Kling 1.6", 12, AspectRatio.LANDSCAPE),
    ("东方古建筑延时", "故宫角楼四季变换延时摄影，云层快速流动", TaskStatus.COMPLETED, "Seedance 2.5", 8, AspectRatio.LANDSCAPE),
    ("咖啡拉花特写", "俯拍咖啡表面拉花成型的微距镜头，暖色调", TaskStatus.COMPLETED, "Kling 1.6", 6, AspectRatio.SQUARE),
    ("雪山日出云海", "日照金山，云海翻涌，广角大场景", TaskStatus.COMPLETED, "Seedance 2.5", 15, AspectRatio.LANDSCAPE),
    ("赛博朋克夜市", "雨后地面倒影映出全息广告牌，慢推镜头", TaskStatus.FAILED, "Kling 1.6", 10, AspectRatio.PORTRAIT),
    ("森林晨雾丁达尔", "阳光穿过树冠形成光柱，缓慢摇镜", TaskStatus.COMPLETED, "Seedance 2.5", 12, AspectRatio.PORTRAIT),
    ("陶瓷手作过程", "双手在转盘上塑形，泥土飞溅特写", TaskStatus.COMPLETED, "Kling 1.6", 9, AspectRatio.SQUARE),
    ("城市天际线转场", "从白天到夜晚的光影渐变转场", TaskStatus.COMPLETED, "Seedance 2.5", 7, AspectRatio.LANDSCAPE),
    ("旗袍女子回眸", "民国风格室内，暖光，浅景深，慢动作回眸", TaskStatus.QUEUED, "Seedance 2.5", 8, AspectRatio.PORTRAIT),
    ("深海发光水母", "幽蓝深海中成群水母漂浮，生物荧光", TaskStatus.COMPLETED, "Kling 1.6", 12, AspectRatio.PORTRAIT),
]

_PROGRESS = {
    TaskStatus.RUNNING: 62,
    TaskStatus.COMPLETED: 100,
}


async def seed(session: AsyncSession, context: SeedContext) -> int:
    rows = [
        VideoTask(
            title=title,
            prompt=prompt,
            status=status.value,
            model=model,
            duration_sec=duration,
            aspect_ratio=ratio.value,
            progress=_PROGRESS.get(status, 0),
            cover_url=placeholder_image(f"sf{i}", 720, 1280),
            author_id=context.demo_user_id,
            author_name=context.demo_user_name,
            favorited=i % 5 == 0,
            downloadable=status is TaskStatus.COMPLETED,
            error_message="上游模型返回超时" if status is TaskStatus.FAILED else None,
            created_at=minutes_ago(i * 47 + 12),
            updated_at=minutes_ago(i * 47 + 10),
            started_at=minutes_ago(i * 47 + 11) if status is not TaskStatus.QUEUED else None,
        )
        for i, (title, prompt, status, model, duration, ratio) in enumerate(_TASKS)
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)
