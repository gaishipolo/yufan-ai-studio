"""字节仓储：`blobs` 表的 upsert、引用计数与回填。

## 为什么 upsert 要包在 savepoint 里

`_ingest` 的整个流程在**一个事务**里：算摘要、查重、配额、写字节、写素材行。
并发上传同一个文件时，两个请求会同时发现「这个 sha256 还没有 blob」，
然后都去插——唯一索引只会让一个成功，另一个拿到 `IntegrityError`。

如果那一步直接 `rollback()`，先写好的素材行会一起消失，而调用方收到的是
「上传失败」。用 `begin_nested()` 把冲突关在 savepoint 里，外层事务继续，
重查一遍就拿到对方建好的 blob。

## 为什么 `ref_count` 由这里维护而不是触发器

触发器在 SQLite / PG 上的写法不同，而这两个库都要跑。更重要的是：
「引用」的业务定义会变（候选算不算？回收站算不算？），定义写在 Python 里
改一次就够，写在触发器里要写两份迁移。

**已知边界**：回填时按「未删除的素材行数」重算，之后由写路径增减。
彻底删除（`purge`）不再减——它删的是已经减过一次的软删行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.asset.library_models import Blob
from modules.asset.lifecycle import FINAL_TIER, blob_tier_for
from modules.asset.models import Asset

__all__ = ["BackfillReport", "BlobRepository", "backfill_blobs"]


@dataclass(slots=True)
class BackfillReport:
    """回填结果。字段名与 `app assets backfill-blobs` 的输出一一对应。"""

    scanned: int = 0
    linked: int = 0
    blobs_created: int = 0
    skipped: int = 0

    def lines(self) -> list[str]:
        return [
            f"扫描 {self.scanned} 条素材：新建 {self.blobs_created} 个 blob、"
            f"关联 {self.linked} 条、跳过 {self.skipped} 条（摘要缺失或无字节）"
        ]


class BlobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_sha256(self, sha256: str) -> Blob | None:
        return await self.session.scalar(select(Blob).where(Blob.sha256 == sha256).limit(1))

    async def get(self, blob_id: str) -> Blob | None:
        return await self.session.get(Blob, blob_id)

    async def ensure(
        self,
        *,
        sha256: str | None,
        size_bytes: int,
        mime_type: str,
        storage_key: str,
        preview_key: str = "",
    ) -> tuple[Blob, bool]:
        """取或建一份字节，返回 `(blob, 是否新建)`。

        `sha256` 为空表示摘要算不出来（历史行、迁移脚本）：这类行**不合并**，
        每条素材各建一个 blob——用「同名同大小」去猜同一份字节，
        猜错的代价是把两份不同的文件当成一份。
        """
        if sha256:
            existing = await self.by_sha256(sha256)
            if existing is not None:
                return existing, False

        blob = Blob(
            sha256=sha256,
            size_bytes=size_bytes,
            mime_type=mime_type,
            storage_key=storage_key,
            preview_key=preview_key,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(blob)
                await self.session.flush()
        except IntegrityError:
            # 并发：另一个请求刚插了同一个 sha256。拿它的那一行，别建第二个。
            if sha256:
                existing = await self.by_sha256(sha256)
                if existing is not None:
                    return existing, False
            raise
        return blob, True

    async def retain(self, blob: Blob) -> None:
        """引用 +1。软删恢复时调用。"""
        blob.ref_count = (blob.ref_count or 0) + 1

    async def release(self, blob_id: str | None) -> None:
        """引用 -1，**不删字节**。

        删字节的时机只有一个：清理任务发现 `ref_count = 0` 且过了宽限期。
        在业务路径上删，等于把「用户手滑点了删除」变成一次不可逆事故。
        """
        if not blob_id:
            return
        blob = await self.session.get(Blob, blob_id)
        if blob is not None and (blob.ref_count or 0) > 0:
            blob.ref_count -= 1

    async def refresh_tier(self, blob_id: str | None, *, grace_days: int = 14) -> None:
        """按「引用它的素材里最高的 stage」重算桶。采纳**不搬字节**，只改这个派生值。

        为什么不搬：几十 MB 的视频挪一次桶就是一次失败点，而失败发生在"采纳"
        这个用户以为已经完成的动作里。改一个字符串、清掉过期时间，字节原地不动。

        `expires_at` 只在 `draft` 时才有意义：宽限期 = 候选过期 + 7 天
        （设计 §6.3 的三级阶梯），所以入库时按 `grace_days` 给一个，
        变 `final` 时清掉。**已经有过期时间的 draft 不往后推**——
        每弃用一次就续 14 天，等于永远不过期。
        """
        if not blob_id:
            return
        blob = await self.session.get(Blob, blob_id)
        if blob is None:
            return
        stages = (
            await self.session.scalars(
                select(Asset.stage).where(
                    Asset.blob_id == blob_id, Asset.deleted_at.is_(None)
                )
            )
        ).all()
        tier = blob_tier_for(stages)
        blob.tier = tier
        if tier == FINAL_TIER:
            blob.expires_at = None
        elif blob.expires_at is None and grace_days > 0:
            blob.expires_at = datetime.now(UTC) + timedelta(days=grace_days)


async def backfill_blobs(
    session: AsyncSession, *, batch_size: int = 500, dry_run: bool = False
) -> BackfillReport:
    """给存量素材补 `blob_id`。幂等、分批、可中断重跑。

    分批的边界是 `blob_id IS NULL`：处理过的行自然离开结果集，所以中断后
    再跑一次就是「从剩下的开始」，不需要游标、也不会重复处理。
    """
    report = BackfillReport()
    while True:
        rows = list(
            (
                await session.scalars(
                    select(Asset)
                    .where(Asset.blob_id.is_(None), Asset.storage_key != "")
                    .order_by(Asset.created_at)
                    .limit(batch_size)
                )
            ).all()
        )
        if not rows:
            break
        report.scanned += len(rows)
        touched = 0
        for row in rows:
            blob, created = await BlobRepository(session).ensure(
                sha256=row.sha256,
                size_bytes=row.size_bytes,
                mime_type=row.mime_type,
                storage_key=row.storage_key,
                preview_key=row.cover_key,
            )
            row.blob_id = blob.id
            blob.ref_count = (blob.ref_count or 0) + 1
            report.linked += 1
            report.blobs_created += 1 if created else 0
            touched += 1
        if dry_run:
            await session.rollback()
            return report
        await session.commit()
        if touched < batch_size:
            break

    report.skipped = int(
        await session.scalar(
            select(func.count())
            .select_from(Asset)
            .where(Asset.blob_id.is_(None), Asset.storage_key == "")
        )
        or 0
    )
    return report
