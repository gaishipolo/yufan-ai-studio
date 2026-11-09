"""候选生命周期的词表与判定（纯函数，不碰数据库）。

## 为什么单独一个文件

这些值域会被四个地方同时引用：`assets` 表的写入路径、列表与分类条的过滤、
周期回收任务、doctor 自检。写散在各处时，最典型的漂移是"列表按 `adopted` 过滤、
配额按 `stage != 'candidate'` 聚合"——两个都像对的，加起来却让弃用的废片继续占额度。

## 三层状态必须分清

| 列 | 回答的问题 | 谁改 |
| --- | --- | --- |
| `stage` | 这份产物**确认了没有** | 采纳 / 弃用 / 过期 |
| `deleted_at` | 用户是否把它**扔进回收站** | 用户 |
| `compliance_status` | 它**能不能被用**（合规） | 审核 |

把它们合成一个枚举是这里最容易犯的错：`deleted` 与 `rejected` 会长成同一个值，
于是"被驳回"和"被删除"共享一条恢复路径——而前者根本不允许恢复。

## 过期为什么不做成"删除"

过期只是把 `stage` 翻成 `expired`：字节还在、配方还在、详情还能打开。
真正回收字节是 `asset.gc` 的事，而且它要等宽限期。混成一步的死法是
"昨天还能看的草稿今天 404"，而用户完全不知道有这个策略。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

__all__ = [
    "COMPLIANCE_STATUSES",
    "DRAFT_TIER",
    "FINAL_TIER",
    "LIVE_STAGE",
    "ORIGINS",
    "STAGES",
    "blob_tier_for",
    "expires_at",
    "is_expired",
]

#: 内容是怎么来的。`upload` 是人给的，`generate` 是 AI 产出的
ORIGINS = ("upload", "generate")

#: 生命周期四态
STAGES = ("candidate", "adopted", "discarded", "expired")

#: 合规四档（B2）。`review_needed` 是"必须人工看一眼"，不是"通过了但备注一下"
COMPLIANCE_STATUSES = ("pending", "approved", "rejected", "review_needed")

#: 唯一"活着"的阶段。只有它能被主体引用、被模板消费、打标签、进向量索引
LIVE_STAGE = "adopted"

#: blob 的两个桶。`draft` 有到期时间、可被回收；`final` 长期保留
FINAL_TIER = "final"
DRAFT_TIER = "draft"


def expires_at(created_at: datetime, ttl_days: int, *, now: datetime | None = None) -> datetime:
    """候选的过期时刻。`ttl_days <= 0` 表示永不过期（返回此刻，调用方据此判断）。

    `now` 只在测试里给：判定要能在没有 `sleep` 的情况下验证。
    """
    base = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    if ttl_days <= 0:
        return base
    return base + timedelta(days=ttl_days)


def is_expired(created_at: datetime, ttl_days: int, *, now: datetime | None = None) -> bool:
    """候选是否已过确认期。到期**不复活**：过期就是过期，只能重新生成一次。

    "恢复过期候选"看起来友好，实际会让 7 天前的中间产物永远留在库里——
    它既不是素材也不是废片，而清理任务永远等不到它。
    """
    if ttl_days <= 0:
        return False
    moment = now or datetime.now(UTC)
    return moment >= expires_at(created_at, ttl_days, now=moment)


def blob_tier_for(stages: Iterable[str]) -> str:
    """由**引用这份字节的素材里最高的 stage** 推出 blob 的桶。

    采纳一张 AI 产物不该触发字节搬迁（几十 MB 的视频挪一次桶就是一次失败点），
    所以桶是派生值：有 `adopted` → `final`，其余 → `draft`（到期可回收）。

    设计文档里还有第三个桶 `original`（上传的高分辨率原片）。这里**不引入**：
    它的保留策略与 `final` 完全一致（都是长期），多一个值只会让
    `draft_used` 那个统计多一个必须记得排除的项，而没有任何行为差别。
    """
    for stage in stages:
        if stage == LIVE_STAGE:
            return FINAL_TIER
    return DRAFT_TIER
