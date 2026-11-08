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

    from db.session import get_session_factory
    from modules.asset.models import Asset
    from modules.asset.schema import AssetSource

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
        usage: list[tuple[str, int]] = []
        if quota:
            usage = list(
                (
                    await session.execute(
                        select(Asset.owner_id, func.sum(Asset.size_bytes))
                        .where(
                            Asset.source == AssetSource.ARCHIVE,
                            Asset.deleted_at.is_(None),
                        )
                        .group_by(Asset.owner_id)
                    )
                ).all()
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

    if not quota:
        report.add(
            "archive.quota", "INFO", "未设置归档容量上限（ARCHIVE_QUOTA_BYTES_PER_USER=0）"
        )
        return

    near = [(owner, int(used or 0)) for owner, used in usage if (used or 0) >= quota * 0.8]
    if near:
        report.add(
            "archive.quota",
            "WARN",
            f"{len(near)} 个用户已用超过配额 80%："
            + "、".join(f"{owner}={used // 1024 // 1024}MB" for owner, used in near[:5]),
        )
    else:
        report.add(
            "archive.quota",
            "PASS",
            f"全部用户归档用量低于配额 80%（上限 {quota} 字节）",
        )
