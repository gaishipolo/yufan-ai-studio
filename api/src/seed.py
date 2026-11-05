"""演示数据装载（仅开发环境使用）。

`python -m cli seed --reset`

账号固定三个，密码写在下面的 `_USERS` 里（仅演示库，生产用 `app create-admin`）。
`u_001` 刻意用固定 id：现有任务的 `author_id` 都是它，不这样写历史数据会变成
「没有人创建的任务」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from core.security import hash_password
from db.session import get_session_factory, init_db
from modules.asset.models import Asset
from modules.identity.models import Role, RolePermission, User, UserRole
from modules.identity.service import IdentityService
from modules.image.models import ImageJob
from modules.shot.models import ShotPreset
from modules.storyboard.models import Storyboard
from modules.task.enums import AspectRatio, TaskStatus
from modules.task.models import VideoTask
from modules.template.models import VideoTemplate

DEMO_AUTHOR = "u_001"
DEMO_AUTHOR_NAME = "老大"

# (id, 用户名, 昵称, 角色标识, 密码) —— 演示库专用口令，生产环境请勿使用
_USERS: list[tuple[str, str, str, str, str]] = [
    # 演示口令也按正式策略（>=10 位、非纯数字）设置：若演示账号自己都不满足策略，
    # 「改密码」这一步就会当场 422，第一印象很糟（R4-14/R4-16）
    ("u_001", "admin", "老大", "admin", "admin@12345"),
    ("u_002", "editor", "小林", "editor", "editor@12345"),
    ("u_003", "viewer", "阿 May", "viewer", "viewer@12345"),
]

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

_TEMPLATES: list[tuple[str, str, str, int, int, str]] = [
    ("电商产品三段式", "痛点开场 → 产品特写 → 场景使用", "电商", 3, 128, "618,新品,转化"),
    ("本地生活探店", "门头 → 招牌菜 → 顾客反应 → 团购引导", "本地生活", 4, 96, "探店,餐饮,团购"),
    ("知识口播模板", "钩子 → 干货三条 → 引导关注", "知识付费", 3, 210, "口播,涨粉"),
    ("旅拍氛围片", "空镜 → 人物入镜 → 情绪特写 → 落日收尾", "文旅", 4, 74, "旅拍,氛围"),
]

_STORYBOARDS: list[tuple[str, str, int, int, AspectRatio]] = [
    ("15 秒快节奏产品片", "三个镜头讲完卖点，适合信息流投放", 3, 15, AspectRatio.PORTRAIT),
    ("30 秒品牌故事", "铺垫情绪，慢节奏运镜，适合品牌号", 6, 30, AspectRatio.LANDSCAPE),
    ("9 宫格产品展示", "方形构图，适合小红书与店铺详情", 9, 18, AspectRatio.SQUARE),
]

_SHOTS: list[tuple[str, str, int, int]] = [
    ("推镜", "缓慢向前推进，主体逐渐充满画面", 3, 980),
    ("拉镜", "镜头后退，揭示环境全貌", 3, 720),
    ("摇镜", "水平摇摄，扫过场景", 4, 640),
    ("环绕", "围绕主体 180 度环绕", 5, 860),
    ("升降", "从地面升到高空俯瞰", 4, 510),
    ("手持跟拍", "手持轻微晃动跟随主体移动", 3, 770),
]

_IMAGE_PROMPTS: list[tuple[str, str]] = [
    ("赛博朋克少女", "赛博朋克风格少女半身像，霓虹光，胶片质感"),
    ("极简产品图", "白色背景上的香水瓶，柔光棚拍"),
    ("水彩风景", "水彩风格的山间小屋，晨雾"),
    ("国潮插画", "国潮风格龙纹插画，朱红与金"),
]


def _ts(minutes_ago: int) -> datetime:
    return datetime.now(tz=UTC) - timedelta(minutes=minutes_ago)


async def seed(*, reset: bool = False) -> dict[str, int]:
    await init_db()

    factory = get_session_factory()
    async with factory() as session:
        if reset:
            # 身份表放最后删：其它表虽然没建外键，但语义上先清业务数据更安全
            for model in (
                VideoTask,
                Asset,
                VideoTemplate,
                Storyboard,
                ShotPreset,
                ImageJob,
                UserRole,
                RolePermission,
                User,
                Role,
            ):
                await session.execute(delete(model))
            await session.commit()

        # 内置角色（admin/editor/viewer）由模块自己保证存在，seed 不重复定义
        identity = IdentityService(session)
        await identity.ensure_builtin_roles()
        roles = {role.code: role for role in await identity.roles.list_all()}

        users = [
            User(
                id=user_id,
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                email=f"{username}@demo.local",
                is_active=True,
            )
            for user_id, username, display_name, _, password in _USERS
        ]
        session.add_all(users)
        await session.flush()
        for (_, _, _, role_code, _), user in zip(_USERS, users, strict=True):
            await identity.users.set_roles(user.id, [roles[role_code].id])

        tasks = [
            VideoTask(
                title=title,
                prompt=prompt,
                status=status.value,
                model=model,
                duration_sec=duration,
                aspect_ratio=ratio.value,
                progress=62 if status is TaskStatus.RUNNING else (100 if status is TaskStatus.COMPLETED else 0),
                cover_url=f"https://picsum.photos/seed/sf{i}/720/1280",
                author_id=DEMO_AUTHOR,
                author_name=DEMO_AUTHOR_NAME,
                favorited=i % 5 == 0,
                downloadable=status is TaskStatus.COMPLETED,
                error_message="上游模型返回超时" if status is TaskStatus.FAILED else None,
                created_at=_ts(i * 47 + 12),
                updated_at=_ts(i * 47 + 10),
                started_at=_ts(i * 47 + 11) if status is not TaskStatus.QUEUED else None,
            )
            for i, (title, prompt, status, model, duration, ratio) in enumerate(_TASKS)
        ]

        assets = [
            Asset(
                name=name,
                type=kind,
                mime_type=f"{kind}/{ext}",
                size_bytes=(1200 + i * 340) * 1024,
                cover_url=f"https://picsum.photos/seed/as{i}/600/600",
                width=w,
                height=h,
                ref_count=refs,
                owner_id=DEMO_AUTHOR,
                created_at=_ts(i * 120 + 30),
            )
            for i, (name, kind, ext, w, h, refs) in enumerate(_ASSETS)
        ]

        templates = [
            VideoTemplate(
                name=name,
                description=desc,
                industry=industry,
                shot_count=shots,
                usage_count=usage,
                cover_url=f"https://picsum.photos/seed/tp{i}/640/360",
                tags=tags,
                created_at=_ts(i * 600 + 120),
            )
            for i, (name, desc, industry, shots, usage, tags) in enumerate(_TEMPLATES)
        ]

        storyboards = [
            Storyboard(
                name=name,
                description=desc,
                shot_count=shots,
                duration_sec=duration,
                aspect_ratio=ratio.value,
                cover_url=f"https://picsum.photos/seed/sb{i}/640/360",
                created_at=_ts(i * 800 + 200),
            )
            for i, (name, desc, shots, duration, ratio) in enumerate(_STORYBOARDS)
        ]

        shots = [
            ShotPreset(
                name=name,
                prompt_fragment=fragment,
                duration_sec=duration,
                cover_url=f"https://picsum.photos/seed/sh{i}/480/270",
                popularity=popularity,
                created_at=_ts(i * 900 + 300),
            )
            for i, (name, fragment, duration, popularity) in enumerate(_SHOTS)
        ]

        images = [
            ImageJob(
                name=prompt[:12],
                prompt=prompt,
                size="1024x1536",
                cover_url=f"https://picsum.photos/seed/img{i}/400/500",
                owner_id=DEMO_AUTHOR,
                created_at=_ts(i * 240 + 60),
            )
            for i, (_, prompt) in enumerate(_IMAGE_PROMPTS)
        ]

        # 各实体类型不同，显式声明为 object 列表（list + list 会被 mypy 判为不兼容）
        rows: list[object] = [*tasks, *assets, *templates, *storyboards, *shots, *images]
        session.add_all(rows)
        await session.commit()

        return {
            "users": len(users),
            "tasks": len(tasks),
            "assets": len(assets),
            "templates": len(templates),
            "storyboards": len(storyboards),
            "shots": len(shots),
            "images": len(images),
        }
