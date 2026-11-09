"""素材模块的演示数据：三个团队、6 个管理员关系、9 条素材、2 个主体、6 张候选。

三种媒体类型都种（图片 / 视频 / 音频），且 `ref_count` 有 0 有正值：素材列表的
「被引用」筛选、以及回收站「仍被引用不能删」这条规则，靠 0 与正值并存才看得到。

## M1 之后为什么这里多了分类与可见域

演示数据是**新结构的样板**，不是「能显示就行」的占位。每条素材现在同时带：

- `category`（旧字符串，M1 期仍是读路径）
- `category_id`（新分类树上的位置）
- `visibility` / `media_type`

只写旧列的话，分类条与库切换器在演示环境里永远是空的，而那是 M2 要验收的界面。
两列同时写、取值一致，正好也给 doctor 的「双写一致性」检查提供了样本。

**不建 blob**：演示素材是远端占位图（`placeholder_image`），没有真实字节，
`storage_key` 为空。这类行 `blob_id` 留空、读路径回落旧列是正确行为，
doctor 只对 `storage_key != ''` 的行要求回填。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.demo import minutes_ago, placeholder_image
from core.teams import TEAM_ACCOUNTS, TEAMS
from modules.asset.archive_service import ArchiveAssetService
from modules.asset.candidate_service import CandidateService, GeneratedDraft
from modules.asset.library_models import Team, TeamMember
from modules.asset.library_service import ensure_tree
from modules.asset.models import Asset
from modules.asset.recipe_service import RecipeDraft, create_recipe
from modules.asset.subject_models import Subject, SubjectMember, SubjectVersion
from modules.asset.tag_models import AssetTag, Tag
from modules.asset.tag_service import ensure_vocabulary

# (名称, 类型, 扩展名, 宽, 高, 引用数, 旧分类, 用途分类名, 团队库)
#
# 最后一列非空表示这条素材在**团队库**里（`visibility=team` + `team_id`）。
# 归属仍写演示账号：`test_module_hooks` 那条守卫要求「演示数据的 owner 必须是
# `ctx.demo_user_id`」，为几个演示行破坏它是本末倒置。演示账号是平台管理员，
# 把素材归档进团队库这件事本身合法——正好也让「团队库里的素材 owner 不是我」
# 这个最容易写错的分支在演示环境里真实存在。
_ASSETS: list[tuple[str, str, str, int, int, int, str, str, str]] = [
    ("冰川场景参考图", "image", "png", 2400, 3200, 4, "scene", "场景背景", ""),
    ("霓虹街道素材", "video", "mp4", 1920, 1080, 2, "source", "参考素材", "t_content"),
    ("品牌片头音效", "audio", "wav", 0, 0, 1, "audio", "参考素材", ""),
    ("产品白底图", "image", "jpg", 2000, 2000, 6, "product", "产品图", "t_content"),
    ("人物参考半身像", "image", "png", 1600, 2400, 3, "character", "人物形象", ""),
    ("城市航拍片段", "video", "mp4", 3840, 2160, 0, "source", "参考素材", ""),
    ("转场音效包", "audio", "mp3", 0, 0, 2, "audio", "参考素材", "t_ops"),
    ("材质贴图-金属", "image", "png", 1024, 1024, 5, "product", "产品图", ""),
    ("片尾字幕背景", "video", "mp4", 1920, 1080, 1, "source", "参考素材", "t_ops"),
]


async def seed(session: AsyncSession, context: SeedContext) -> int:
    """种团队、成员关系与素材。三者一起提交：半个演示库比没有更难查。"""
    teams = [Team(id=spec.id, name=spec.name, kind=spec.kind) for spec in TEAMS]
    members = [
        TeamMember(team_id=account.team_id, user_id=account.user_id, role="admin")
        for account in TEAM_ACCOUNTS
    ]
    session.add_all([*teams, *members])
    await session.flush()

    # 三棵树的初始内容与生产路径同一个函数，避免「seed 建的和运行时建的不一样」。
    # 公共库必须在这里也建：`app seed --reset` 会按 metadata 清表，
    # 迁移里建过的那棵会被清掉。
    await ensure_tree(session, "brand", "", commit=False)
    team_trees: dict[str, dict[str, str]] = {}
    # 变量叫 `team_row` 而不是 `team`：下面 `_ASSETS` 的解包元组里也有一个
    # `team`（字符串 id），同名会让 mypy 认定后面那个是 Team 对象，
    # 而错误信息指向的是 `team_trees.get(team)` 这种看不出问题的地方。
    for team_row in teams:
        team_trees[team_row.id] = await ensure_tree(session, "team", team_row.id, commit=False)
    tree = await ensure_tree(session, "personal", context.demo_user_id, commit=False)

    rows: list[Asset] = []
    for i, (name, kind, ext, w, h, refs, legacy, purpose, team) in enumerate(_ASSETS):
        stamp = minutes_ago(i * 120 + 30)
        rows.append(
            Asset(
                name=name,
                type=kind,
                media_type=kind,
                mime_type=f"{kind}/{ext}",
                size_bytes=(1200 + i * 340) * 1024,
                cover_url=placeholder_image(f"as{i}", 600, 600),
                width=w,
                height=h,
                ref_count=refs,
                owner_id=context.demo_user_id,
                category=legacy,
                # `category_id` 必须落在**这条素材所属库**的树上：指向个人树的
                # 团队素材会让分类条计数与「未分类」判断同时错一半，而症状只是
                # 「团队库里分类点进去是空的」——最容易归咎于缓存。
                category_id=(team_trees.get(team) or tree).get(purpose, ""),
                visibility="team" if team else "personal",
                team_id=team,
                created_at=stamp,
                # 演示素材是**上传件**：`origin=upload` / `stage=adopted` /
                # 合规 `approved`。三列都要显式写——列级默认值是"生成件的假设"
                # （`stage=adopted` 之外那两列的默认值分别是 upload 与 pending），
                # 漏写合规的表现是"演示主体发布不了"，而报错只说合规未通过。
                origin="upload",
                stage="adopted",
                compliance_status="approved",
                adopted_at=stamp,
            )
        )
    session.add_all(rows)
    await session.flush()

    # 标签：先补齐词表，再按「词 → 素材下标」贴上去。
    # 只贴其中几条而不是全打满：筛选条要能看出「有的词在用、有的词零使用」——
    # 全打满的话常显区与"＋更多"的差异在演示环境里看不出来。
    await ensure_vocabulary(session, commit=False)
    tags = {
        (tag.dimension, tag.name): tag
        for tag in (await session.scalars(select(Tag))).all()
    }
    plan: dict[tuple[str, str], list[int]] = {
        ("style", "写实摄影"): [0, 3, 8],
        ("style", "电影感"): [1, 5, 8],
        ("style", "极简留白"): [3],
        ("scene", "棚拍纯色"): [3],
        ("scene", "城市街景"): [1, 5],
        ("scene", "自然户外"): [0],
        ("shot", "全景"): [0, 5],
        ("shot", "特写"): [3, 4],
        ("composition", "无人"): [0, 3, 7],
        ("composition", "单人"): [4],
        ("placement", "主图"): [3, 7],
        ("placement", "封面"): [8],
        ("content", "无水印"): [0, 1, 3, 4, 5, 7, 8],
    }
    links = [
        AssetTag(asset_id=rows[index].id, tag_id=tags[key].id, added_by=context.demo_user_id)
        for key, indexes in plan.items()
        if key in tags  # 词表改动后这里不该整段失败，缺词就跳过
        for index in indexes
        if index < len(rows)
    ]
    session.add_all(links)
    created = await _seed_subjects(session, context, rows, tree)
    created += await _seed_candidates(session, context, rows)
    await session.commit()
    return len(rows) + len(teams) + len(members) + len(links) + created


#: 演示候选：`(批次名, 提示词, 张数, 参考素材下标)`。
#:
#: 两批而不是一批：候选确认区按 `recipe_id` 分组，只有一批时"分组"这件事
#: 在演示环境里看不出来——而"同批必须挨着"正是那一块的验收点。
_CANDIDATE_BATCHES: list[tuple[str, str, int, int]] = [
    ("定妆照 · 正面半身", "studio portrait, soft key light, front view, 85mm", 4, 4),
    ("材质特写 · 阳极氧化铝", "macro shot of anodized aluminium, raking light, dust free", 2, 7),
]


async def _seed_candidates(
    session: AsyncSession, context: SeedContext, rows: list[Asset]
) -> int:
    """两批待确认的生成产物（共 6 张）。

    **走真实写路径**（`create_recipe` + `register_generated`）而不是直接建行：
    候选的三条不变量（`origin=generate` / `stage=candidate` / `compliance=pending`）
    就是靠这条路径保证的，seed 绕过它等于让演示数据证明不了一件事。

    参考图指向演示素材：这样详情浮层的"生成血缘"一打开就有东西，
    而血缘里的那张图又能点开——这条链在演示环境里必须是真的。
    """
    service = CandidateService(session, ArchiveAssetService(session))
    created = 0
    for index, (title, prompt, count, reference) in enumerate(_CANDIDATE_BATCHES):
        recipe = await create_recipe(
            session,
            RecipeDraft(
                model="seedream",
                model_version="3.0",
                provider="volcengine",
                prompt=prompt,
                negative_prompt="blurry, extra fingers, watermark",
                params={"size": "1024x1024", "steps": 28, "cfg": 7.5},
                seed="8848213",
                seed_requested="-1",
                # AIGC 标识只做元数据留痕（画面上不加可见水印）
                content_label={"aigc": True, "labelVersion": "v1", "generator": "yufan"},
                references=((rows[reference].id, "reference"),) if reference < len(rows) else (),
                cost_units=0.4,
                created_by=context.demo_user_id,
            ),
        )
        for shot in range(count):
            await service.register_generated(
                context.demo_user_id,
                GeneratedDraft(
                    name=f"{title} #{shot + 1}",
                    kind="image",
                    mime_type="image/png",
                    cover_url=placeholder_image(f"cand{index}{shot}", 512, 512),
                    width=1024,
                    height=1024,
                    size_bytes=(480 + shot * 40) * 1024,
                    # 错开时间：一批里最新的一张排在最前，横幅的排序才可验证
                    created_at=minutes_ago(index * 90 + shot * 5 + 12),
                ),
                recipe,
            )
            created += 1
    return created


async def _seed_subjects(
    session: AsyncSession,
    context: SeedContext,
    rows: list[Asset],
    tree: dict[str, str],
) -> int:
    """两个主体：一个可发布（三态里的 `ready`），一个缺能力（`incomplete`）。

    只给"能用的"会让可用性角标永远只有一种颜色，而那个角标正是这一批要看的东西；
    只给"缺的"又会让模板侧没有可调用的对象。所以一个 published、一个 draft。

    **演示主体不接 `subject_legacy_packs`**：旧包由模板模块的 seed 在下一步（`seed_order=30`）
    才建出来，这里还看不到它们的 id。这不是遗漏——"包还没有主体"正是双读期里
    真实存在的状态，`shadow` 模式的差异日志本来就该把它数出来。
    """
    # 只用**个人库**里的图：索引 4（人物）、0（场景）、7（材质）三条。
    # 团队库里的素材不属于演示账号的私人主体。
    character, scene, texture = rows[4], rows[0], rows[7]

    published = Subject(
        subject_type="character",
        name="示例主体 · 人物替换",
        owner_id=context.demo_user_id,
        visibility="personal",
        category_id=tree.get("人物形象", ""),
        cover_asset_id=character.id,
        attributes={"发型": "齐肩", "肤色": "自然"},
        requires=["generate", "download"],
        required_roles=["front", "scene"],
        status="published",
        current_version=1,
        revision=1,
        published_at=datetime.now(UTC),
    )
    draft = Subject(
        subject_type="product",
        name="示例主体 · 产品细节待补",
        owner_id=context.demo_user_id,
        visibility="personal",
        category_id=tree.get("产品图", ""),
        cover_asset_id=texture.id,
        attributes={"材质": "阳极氧化铝"},
        # 素材本身可生成，但**还没开下载**：这就是 `incomplete` 的典型形态，
        # 卡片上写"缺少 download"，详情里能点到是哪一张不给
        requires=["generate", "download"],
        required_roles=["front", "detail"],
        status="draft",
        revision=1,
    )
    session.add_all([published, draft])
    await session.flush()

    members = [
        SubjectMember(
            subject_id=published.id,
            asset_id=character.id,
            role="front",
            sort_order=0,
            grants=["generate", "download"],
            in_output=True,
            added_by=context.demo_user_id,
        ),
        SubjectMember(
            subject_id=published.id,
            asset_id=scene.id,
            role="scene",
            sort_order=1,
            grants=["generate", "download"],
            in_output=True,
            added_by=context.demo_user_id,
        ),
        SubjectMember(
            subject_id=draft.id,
            asset_id=texture.id,
            role="front",
            sort_order=0,
            grants=["generate"],
            in_output=True,
            added_by=context.demo_user_id,
        ),
    ]
    session.add_all(members)

    session.add(
        SubjectVersion(
            subject_id=published.id,
            version=1,
            members=[
                {
                    "asset_id": character.id,
                    "sha256": character.sha256 or "",
                    "role": "front",
                    "grants": ["generate", "download"],
                    "in_output": True,
                    "sort_order": 0,
                },
                {
                    "asset_id": scene.id,
                    "sha256": scene.sha256 or "",
                    "role": "scene",
                    "grants": ["generate", "download"],
                    "in_output": True,
                    "sort_order": 1,
                },
            ],
            cover_asset_id=published.cover_asset_id,
            attributes=dict(published.attributes),
            requires=list(published.requires),
            required_roles=list(published.required_roles),
            published_at=published.published_at,
            published_by=context.demo_user_id,
        )
    )
    return 2 + len(members) + 1
