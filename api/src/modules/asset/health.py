"""素材模块的部署自检：`ModuleSpec.doctor` 指向这里。

三条检查都只对素材模块成立（归档镜像、镜像对账、归档配额），所以它们属于
模块而不是 `api/diagnostics.py`。历史上它们写在诊断层里，代价是**框架为了
三条业务检查反向 import `modules.asset.models`**——删掉素材模块，`app doctor`
会在第 5 项上 `ModuleNotFoundError` 崩掉，后面十几项一条都看不到。

写模块自检时只有一条纪律：**只往报告里加自己的条目**，不改别人的结论。
名称统一 `archive.*`（沿用旧名，运维已经按这个名字找），新增的用
`<模块>.<主题>`。
"""

from __future__ import annotations

from api.module import DoctorReportLike
from core.config import settings
from infra.archive import get_archive_storage

__all__ = ["check"]


async def check(report: DoctorReportLike) -> None:
    """归档镜像一致性与配额水位。

    两项数据的来源完全不同：镜像是**对象存储里的 JSON 文件** vs **DB 里的行**，
    配额是**按 owner 聚合的字节数**。放在一起是因为它们都读同一批归档行，
    合成一次查询比拆成两个钩子省一轮扫描。
    """
    from sqlalchemy import func, select

    from core.teams import TEAM_ADMIN_COUNT, TEAMS
    from db.session import get_session_factory
    from modules.asset.library_models import Category, Team, TeamMember
    from modules.asset.models import Asset
    from modules.asset.schema import AssetSource
    from modules.asset.subject_models import Subject, SubjectMember
    from modules.asset.tag_models import AssetTag, Tag
    from modules.asset.tag_vocabulary import TAG_TOTAL

    async with get_session_factory()() as session:
        archive_ids = set(
            (
                await session.execute(
                    select(Asset.id).where(Asset.source == AssetSource.ARCHIVE)
                )
            )
            .scalars()
            .all()
        )
        quota = max(0, settings.archive_quota_bytes_per_user)

        # 库边界的三条对账：团队数、管理员数、每个团队都有收件箱。
        # 只对「没有任何团队」这一种情况给 WARN：那是迁移跑了但 seed 没跑，
        # 顶栏切换器会是空的，而素材页看起来完全正常。
        team_rows = list(
            (
                await session.execute(
                    select(Team.id, Team.name, Team.status).where(Team.status == "active")
                )
            ).all()
        )
        admin_count = int(
            await session.scalar(
                select(func.count())
                .select_from(TeamMember)
                .where(TeamMember.role == "admin")
            )
            or 0
        )
        tree_keys = {
            (row[0], row[1])
            for row in (
                await session.execute(
                    select(Category.scope, Category.owner_key).where(Category.is_inbox.is_(True))
                )
            ).all()
        }
        # 回填对账只认真有字节的行：演示素材是远端占位图，没有 storage_key，
        # 它们永远不会有 blob，把它们算成「未回填」会让这条检查永久发红。
        pending_blobs = int(
            await session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(Asset.blob_id.is_(None), Asset.storage_key != "")
            )
            or 0
        )

    storage = get_archive_storage()
    mirror_ids = {
        key.rsplit("/", 1)[-1].removesuffix(".json")
        for key in storage.list_keys("catalog/assets", suffix=".json")
    }
    missing = sorted(archive_ids - mirror_ids)
    orphan = sorted(mirror_ids - archive_ids)
    if missing or orphan:
        report.add(
            "archive.mirrors",
            "WARN",
            f"缺镜像 {len(missing)} 条（模板 worker 读不到），孤儿镜像 {len(orphan)} 份；"
            "跑 app rebuild-mirrors 按 DB 幂等重建。"
            "注意 app import-archive-index 是 JSON→DB 的单向导入，方向相反，修不了缺镜像",
        )
    else:
        report.add("archive.mirrors", "PASS", f"DB 归档行与 JSON 镜像一致（{len(archive_ids)} 条）")

    if settings.archive_mirror_enabled:
        report.add(
            "archive.mirror.reconcile",
            "PASS",
            f"周期对账已启用（每 {settings.archive_mirror_interval_sec:g}s，幂等，无需选主）",
        )
    else:
        report.add(
            "archive.mirror.reconcile",
            "WARN",
            "周期对账已关闭：投影失败只能靠人工跑 app rebuild-mirrors 收敛",
        )

    # ---- 库边界（M1）：团队 / 分类树 / 字节回填 ----
    declared = {spec.id for spec in TEAMS}
    found = {row[0] for row in team_rows}
    if not team_rows:
        report.add(
            "library.teams",
            "WARN",
            "没有任何团队：顶栏库切换器会是空的，而素材页看起来完全正常。"
            "跑 app seed（或 app seed --reset）建初始团队",
        )
    elif declared - found:
        report.add(
            "library.teams",
            "WARN",
            f"声明了但库里没有的团队：{', '.join(sorted(declared - found))}；"
            "检查 seed 是否跑过，或这些团队被归档了",
        )
    else:
        report.add(
            "library.teams",
            "PASS",
            f"{len(team_rows)} 个团队、{admin_count} 名团队管理员"
            f"（声明 {TEAM_ADMIN_COUNT} 名）",
        )

    missing_trees = [
        team_id for team_id in found if ("team", team_id) not in tree_keys
    ]
    if ("brand", "") not in tree_keys:
        missing_trees.append("brand")
    if missing_trees:
        report.add(
            "library.categories",
            "WARN",
            f"{len(missing_trees)} 个库没有分类树（{', '.join(missing_trees[:5])}）；"
            "重启一次即由 bootstrap 补齐，或跑 app seed",
        )
    else:
        report.add(
            "library.categories",
            "PASS",
            f"{len(tree_keys)} 棵分类树各有且只有一个收件箱",
        )

    # ---- 标签词表（M2）：词表补齐与孤儿关联 ----
    tag_total = int(await session.scalar(select(func.count()).select_from(Tag)) or 0)
    live_tags = int(
        await session.scalar(
            select(func.count())
            .select_from(Tag)
            .where(Tag.status == "active", Tag.merged_into == "")
        )
        or 0
    )
    orphans = int(
        await session.scalar(
            select(func.count())
            .select_from(AssetTag)
            .where(AssetTag.tag_id.not_in(select(Tag.id)))
        )
        or 0
    )
    if orphans:
        report.add(
            "library.tags",
            "WARN",
            f"{orphans} 条素材标签关联指向不存在的词；筛选时这些关联会被静默忽略，"
            "把标签页做出来之前先清掉它（`DELETE FROM asset_tags WHERE tag_id NOT IN ...`）",
        )
    elif live_tags < TAG_TOTAL:
        report.add(
            "library.tags",
            "WARN",
            f"词表只有 {live_tags} 个可用词（初始 {TAG_TOTAL} 个）；"
            "重启一次即由 bootstrap 补齐，或跑 app seed",
        )
    else:
        report.add(
            "library.tags",
            "PASS",
            f"词表 {tag_total} 行，其中 {live_tags} 个可用（初始 {TAG_TOTAL} 个）",
        )

    if pending_blobs:
        report.add(
            "library.blobs",
            "WARN",
            f"{pending_blobs} 条素材还没有 blob 记录（读路径会回落旧列，功能不受影响）；"
            "跑 app assets backfill-blobs 补齐，M1-G 删旧列前必须清零",
        )
    else:
        report.add("library.blobs", "PASS", "有字节的素材都已登记 blob")

    # ---- 主体（M2.5）：成员孤儿与缺图 ----
    # 这两条都不是"报错"，而是**主体能不能被发布**的前置：成员的图被删了，
    # 主体会进「缺图」态、发布被 409 拦下，而界面只会说"有 1 张成员图已被删除"。
    # 运维在 doctor 里一次看到全貌，比逐个主体点开要快。
    subject_total = int(await session.scalar(select(func.count()).select_from(Subject)) or 0)
    orphan_members = int(
        await session.scalar(
            select(func.count())
            .select_from(SubjectMember)
            .where(SubjectMember.subject_id.not_in(select(Subject.id)))
        )
        or 0
    )
    missing_members = int(
        await session.scalar(
            select(func.count())
            .select_from(SubjectMember)
            .join(Asset, Asset.id == SubjectMember.asset_id)
            .where(Asset.deleted_at.is_not(None))
        )
        or 0
    )
    empty_published = int(
        await session.scalar(
            select(func.count())
            .select_from(Subject)
            .where(Subject.status == "published", Subject.current_version == 0)
        )
        or 0
    )
    if orphan_members:
        report.add(
            "library.subjects",
            "WARN",
            f"{orphan_members} 条主体成员指向不存在的主体；成员是「当时用了哪张图」的证据，"
            "清之前先确认这些主体确实不该存在（铁律①：删主体不删成员）",
        )
    elif missing_members:
        report.add(
            "library.subjects",
            "WARN",
            f"{missing_members} 个主体成员指向已删除的素材：这些主体会停在「缺图」态、"
            "无法发布；去回收站恢复，或编辑主体换掉那一张",
        )
    elif empty_published:
        report.add(
            "library.subjects",
            "WARN",
            f"{empty_published} 个主体已是 published 但 current_version 为 0："
            "模板按 current_version 取图，这类主体永远取不到版本（多半是手工改库留下的）",
        )
    else:
        report.add(
            "library.subjects",
            "PASS",
            f"{subject_total} 个主体，成员无孤儿、无缺图，已发布主体都有版本号",
        )

    # ---- 候选与生成血缘（M3）----
    # 三条都是"只在演示环境之外才会出现"的问题，所以必须在 doctor 里能看见：
    # ①生成件没有配方 → 血缘断了，但列表看起来完全正常；
    # ②超时未确认的候选还挂在 `candidate` → 回收任务没跑，横幅永远挂着坏图；
    # ③候选被成员引用 → 绕过采纳直接进了主体，是硬错误。
    from datetime import UTC, datetime

    from modules.asset.lifecycle import is_expired

    candidate_rows = (
        await session.execute(
            select(Asset.id, Asset.created_at).where(Asset.stage == "candidate")
        )
    ).all()
    orphan_recipe = int(
        await session.scalar(
            select(func.count())
            .select_from(Asset)
            .where(Asset.origin == "generate", Asset.recipe_id.is_(None))
        )
        or 0
    )
    in_subject = int(
        await session.scalar(
            select(func.count())
            .select_from(SubjectMember)
            .join(Asset, Asset.id == SubjectMember.asset_id)
            .where(Asset.stage == "candidate")
        )
        or 0
    )
    ttl = settings.archive_candidate_ttl_days
    stale = sum(
        1
        for _, created_at in candidate_rows
        if created_at is not None and is_expired(created_at, ttl, now=datetime.now(UTC))
    )
    if in_subject:
        report.add(
            "library.candidates",
            "WARN",
            f"{in_subject} 张候选被主体成员引用：候选只能在采纳后进入主体（M3-B 的铁律），"
            "这些行要么补采纳，要么从主体里移除",
        )
    elif orphan_recipe:
        report.add(
            "library.candidates",
            "WARN",
            f"{orphan_recipe} 条生成件没有配方：血缘断了，详情浮层的「生成血缘」会是空的。"
            "查一下生成入库路径有没有漏写 recipe_id",
        )
    elif stale:
        report.add(
            "library.candidates",
            "WARN",
            f"{stale} 张候选已超过确认期（{ttl} 天）仍是 candidate："
            "候选过期扫描没跑（ASSET 模块的 asset.candidate.gc），"
            "用户点采纳会得到 409",
        )
    else:
        report.add(
            "library.candidates",
            "PASS",
            f"{len(candidate_rows)} 张候选（确认期 {ttl} 天），无孤儿配方、无越权引用",
        )

    # ---- 配额（A6：按可见域聚合、sha256 去重） ----
    # 三种额度分开报：个人账面、团队账面、物理。合成一个数字就解释不了
    # 「删了一堆空间没变」（回收站）和「复制一次额度翻倍」（没去重）。
    team_quota = max(0, settings.archive_quota_bytes_per_team)
    physical_quota = max(0, settings.archive_quota_bytes_total)
    if not (quota or team_quota or physical_quota):
        report.add(
            "archive.quota",
            "INFO",
            "未设置归档容量上限（ARCHIVE_QUOTA_BYTES_PER_USER / _PER_TEAM / _TOTAL 均为 0）",
        )
        return

    from modules.asset.archive_repository import ArchiveAssetRepository

    async with get_session_factory()() as session:
        repo = ArchiveAssetRepository(session)
        physical = await repo.physical_used()
        owners = list((await session.scalars(select(Asset.owner_id).distinct())).all())
        personal = [
            (owner, await repo.distinct_used(visibility="personal", owner_id=owner))
            for owner in owners
        ]
        team_ids = [row[0] for row in (await session.execute(select(Team.id))).all()]
        teams = [
            (team_id, await repo.distinct_used(visibility="team", team_id=team_id))
            for team_id in team_ids
        ]

    if quota:
        near = [(owner, used) for owner, used in personal if used >= quota * 0.8]
        if near:
            report.add(
                "archive.quota",
                "WARN",
                f"{len(near)} 个用户已用超过个人配额 80%："
                + "、".join(f"{owner}={used // 1024 // 1024}MB" for owner, used in near[:5]),
            )
        else:
            report.add(
                "archive.quota",
                "PASS",
                f"全部用户低于个人配额 80%（上限 {quota} 字节，按 sha256 去重）",
            )
    else:
        report.add("archive.quota", "INFO", "未设置个人配额上限（按可见域与去重口径）")

    if team_quota:
        hot = [(team_id, used) for team_id, used in teams if used >= team_quota * 0.8]
        if hot:
            report.add(
                "archive.team_quota",
                "WARN",
                f"{len(hot)} 个团队已用超过团队配额 80%："
                + "、".join(f"{team_id}={used // 1024 // 1024}MB" for team_id, used in hot[:5]),
            )
        else:
            report.add("archive.team_quota", "PASS", f"全部团队低于配额 80%（上限 {team_quota}）")

    if physical_quota:
        if physical > physical_quota:
            report.add(
                "archive.physical_quota",
                "FAIL",
                f"物理占用 {physical // 1024 // 1024}MB 已超上限 "
                f"{physical_quota // 1024 // 1024}MB；新上传会被拒，需清理回收站或扩容",
            )
        else:
            report.add(
                "archive.physical_quota",
                "PASS",
                f"物理占用 {physical // 1024 // 1024}MB / 上限 {physical_quota // 1024 // 1024}MB",
            )
