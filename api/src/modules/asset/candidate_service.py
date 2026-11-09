"""候选生命周期：入库、确认区、采纳 / 弃用 / 过期（M3-B）。

## 候选是"待办"，不是"分类"

它不出现在 `GET /assets`（默认 `stage=adopted`），只在顶部横幅里按批出现。
这不是视图偏好，而是三条硬约束的前提：

| 约束 | 在哪实现 |
| --- | --- |
| 候选不进列表 | `ArchiveAssetRepository.list_visible` 的 `stage` 过滤 |
| 候选不计配额 | `distinct_used` 的 `stage` 过滤 |
| 候选不进主体 / 不能打标签 | 采纳是唯一的入口，写路径只看 `adopted` |

三条**各写一次**而不是抽一个"可见素材"查询：抽出来之后，新增一条查询路径
很容易绕开它，而绕开的表现是"候选悄悄占了额度"——一个要等对账才会发现的 bug。

## 采纳为什么要求"本人"

`generated_by == 当前用户` 是 A4 的结论：候选的归属恒等于生成者，
所以"别人能不能采纳我的草稿"这个问题不需要权限码来回答，它根本不存在。
团队共享**成品**，不共享未确认的中间产物。

## 采纳是幂等的

重试、双击、批量重试都会打到已经 `adopted` 的行。回 409 会让"全选采纳"
在第二次点击时整批失败，而用户看不出是自己点重了。所以已采纳直接返回现状。
`discarded` / `expired` 则不同——那两种状态是**用户/时间做的决定**，
再采纳等于撤销决定，必须显式（重新生成），不能靠重试穿透。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from core import audit
from core.config import settings
from core.exceptions import BadRequest, NotFound
from core.logging import get_logger
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.archive_service import ArchiveAssetService
from modules.asset.blob_repository import BlobRepository
from modules.asset.candidate_schema import (
    AdoptRequest,
    BatchAdoptRequest,
    BatchAdoptResult,
    CandidateGroup,
    CandidateList,
)
from modules.asset.errors import CandidateClosed, ComplianceRejected
from modules.asset.library_service import INBOX_NAME, ensure_tree
from modules.asset.lifecycle import LIVE_STAGE, expires_at, is_expired
from modules.asset.models import Asset
from modules.asset.recipe_models import GenerationRecipe
from modules.asset.recipe_service import summarize
from modules.asset.schema import AssetSource
from modules.asset.tag_service import TagService

logger = get_logger(__name__)

__all__ = ["CandidateService", "GeneratedDraft", "expire_candidates"]

#: 确认区一次最多拉几批。候选是待办，堆到几百条时问题在生成侧（没人确认），
#: 而不是在列表分页——给一批上限 + 一句"还有更多"比伪分页诚实。
_MAX_CANDIDATES = 200


@dataclass(frozen=True, slots=True)
class GeneratedDraft:
    """一条待确认的产物。**没有 `category_id`**：候选还没有"家"。

    分类在采纳时补（`AdoptRequest.category_id`）。入库就写分类会让候选
    出现在分类树的角标里——而它既不是素材也不该被统计。
    """

    name: str
    kind: str
    mime_type: str = ""
    cover_url: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    size_bytes: int = 0
    created_at: datetime | None = None


class CandidateService:
    def __init__(self, session, archive: ArchiveAssetService) -> None:
        self.session = session
        self.archive = archive
        self.repo = ArchiveAssetRepository(session)
        self.blobs = BlobRepository(session)

    # ------------------------------------------------------------------ 入库
    async def register_generated(
        self, owner: str, draft: GeneratedDraft, recipe: GenerationRecipe
    ) -> Asset:
        """登记一条刚生成出来的候选。**不 commit**：由调用方掌握事务边界。

        三件事必须同时发生，否则会出现"看起来是候选、行为像成品"的行：
        `origin=generate`（合规的依据）、`stage=candidate`（不进列表与配额）、
        `compliance_status=pending`（默认没通过，等审核）。
        """
        now = draft.created_at or datetime.now(UTC)
        row = Asset(
            name=draft.name[:200],
            type=draft.kind,
            media_type=draft.kind,
            mime_type=draft.mime_type,
            size_bytes=draft.size_bytes,
            cover_url=draft.cover_url,
            width=draft.width,
            height=draft.height,
            duration=draft.duration,
            owner_id=owner,
            source=AssetSource.ARCHIVE,
            visibility="personal",
            category="",
            category_id="",
            recipe_id=recipe.id,
            origin="generate",
            stage="candidate",
            compliance_status="pending",
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    # ------------------------------------------------------------------ 读
    async def groups(self, owner: str) -> CandidateList:
        """按配方分组（同批的几张必须挨着）。过期的不返回，也不在这里改状态。"""
        ttl = settings.archive_candidate_ttl_days
        rows = (
            await self.session.scalars(
                select(Asset)
                .where(
                    Asset.owner_id == owner,
                    Asset.source == AssetSource.ARCHIVE,
                    Asset.stage == "candidate",
                    Asset.deleted_at.is_(None),
                )
                .order_by(Asset.created_at.desc(), Asset.id.desc())
                .limit(_MAX_CANDIDATES)
            )
        ).all()
        live = [row for row in rows if not is_expired(row.created_at, ttl)]
        if not live:
            return CandidateList()

        recipes = {
            recipe.id: recipe
            for recipe in (
                await self.session.scalars(
                    select(GenerationRecipe).where(
                        GenerationRecipe.id.in_(
                            {row.recipe_id for row in live if row.recipe_id}
                        )
                    )
                )
            ).all()
        }
        # 分组用插入序：上面已按创建时间倒序，所以最新的批次排在最前
        grouped: dict[str, list[Asset]] = {}
        for row in live:
            grouped.setdefault(row.recipe_id or "", []).append(row)

        groups: list[CandidateGroup] = []
        for recipe_id, items in grouped.items():
            recipe = recipes.get(recipe_id)
            stamps = [expires_at(row.created_at, ttl) for row in items if row.created_at]
            groups.append(
                CandidateGroup(
                    recipe_id=recipe_id,
                    summary=_summary(recipe, len(items), items[0]),
                    prompt_summary=summarize(recipe.prompt) if recipe is not None else "",
                    created_at=items[0].created_at,
                    # 取组内**最早**的到期时刻：一张到期，整组就该消失
                    expires_at=min(stamps) if stamps else None,
                    items=[self.archive.to_out(row, owner) for row in items],
                )
            )
        return CandidateList(groups=groups, total=len(live))

    # ------------------------------------------------------------------ 写
    async def adopt(self, asset_id: str, owner: str, payload: AdoptRequest) -> Asset:
        row = await self._open(asset_id, owner)
        if row.stage == LIVE_STAGE:
            return row
        category_id = await self._category(owner, payload.category_id)
        row.stage = LIVE_STAGE
        row.adopted_at = datetime.now(UTC)
        row.category_id = category_id
        await self.blobs.refresh_tier(row.blob_id)
        await self.session.commit()
        if payload.tag_ids:
            # commit 之后再贴标签：`set_asset_tags` 自己会 commit（它要给
            # `tags.last_used_at` 落值），放在采纳之前会把"还没采纳"的中间态
            # 一起提交掉
            await TagService(self.session).set_asset_tags(asset_id, payload.tag_ids, owner)
        await audit.record(
            "asset.candidate.adopt",
            actor_id=owner,
            target_type="asset",
            target_id=asset_id,
            detail={"name": row.name, "recipeId": row.recipe_id or ""},
        )
        return row

    async def adopt_many(
        self, owner: str, payload: BatchAdoptRequest
    ) -> BatchAdoptResult:
        """整组采纳。**先全部校验，再全部写**：半成功是最坏的形态。

        先校验是因为"选了 8 张、成了 5 张"没法解释——用户不知道差的那 3 张是
        过期了、被驳回、还是网络断了，而重试又有可能重复采纳。
        """
        rows: list[Asset] = []
        already_rows: list[Asset] = []
        for asset_id in dict.fromkeys(payload.asset_ids):
            row = await self._open(asset_id, owner)
            (already_rows if row.stage == LIVE_STAGE else rows).append(row)
        if not rows:
            return BatchAdoptResult(
                items=[self.archive.to_out(row, owner) for row in already_rows],
                already=len(already_rows),
            )

        tag_ids = await TagService(self.session).resolve(payload.tag_ids)
        category_id = await self._category(owner, payload.category_id)
        now = datetime.now(UTC)
        for row in rows:
            row.stage = LIVE_STAGE
            row.adopted_at = now
            row.category_id = category_id
            await self.blobs.refresh_tier(row.blob_id)
        await self.session.commit()
        if tag_ids:
            for row in rows:
                await TagService(self.session).set_asset_tags(row.id, tag_ids, owner)
        await audit.record(
            "asset.candidate.adopt",
            actor_id=owner,
            target_type="asset",
            detail={"count": len(rows), "ids": [row.id for row in rows]},
        )
        return BatchAdoptResult(
            items=[self.archive.to_out(row, owner) for row in rows],
            already=len(already_rows),
        )

    async def discard(self, asset_id: str, owner: str) -> Asset:
        """弃用：字节留着等回收，配方留着解释血缘。"""
        row = await self._open(asset_id, owner)
        if row.stage == "discarded":
            return row
        row.stage = "discarded"
        await self.blobs.refresh_tier(row.blob_id)
        await self.session.commit()
        await audit.record(
            "asset.candidate.discard",
            actor_id=owner,
            target_type="asset",
            target_id=asset_id,
            detail={"name": row.name},
        )
        return row

    # ------------------------------------------------------------------ 内部
    async def _open(self, asset_id: str, owner: str) -> Asset:
        """取一个还开着的候选。

        `discarded` / `expired` / 已过期（时间到了但回收任务还没跑）三种情况
        回同一个 409：对调用方来说它们都是"这条不能再采纳了"，
        而区分它们只会让前端多写三个分支却显示同一句话。
        """
        row = await self.repo.get(asset_id)
        if row.owner_id != owner:
            raise NotFound("候选不存在")
        if row.origin != "generate":
            # 采纳一个上传件是没有意义的状态转换：它本来就是素材
            raise BadRequest("只有 AI 生成的产物是候选")
        if row.stage == LIVE_STAGE:
            return row
        if row.stage in ("discarded", "expired") or is_expired(
            row.created_at, settings.archive_candidate_ttl_days
        ):
            raise CandidateClosed()
        if row.compliance_status == "rejected":
            raise ComplianceRejected()
        return row

    async def _category(self, owner: str, category_id: str) -> str:
        """落分类：给的就校验它属于**个人库**那棵树，没给就进收件箱。"""
        tree = await ensure_tree(self.session, "personal", owner, commit=False)
        if not category_id:
            return tree.get(INBOX_NAME, "")
        if category_id not in set(tree.values()):
            # 团队树的分类不能当个人素材的家：可见域是单一值，落在别人的树上
            # 会让"这条属于哪个库"变成不可判定
            raise BadRequest("分类不存在或不属于个人库")
        return category_id


async def expire_candidates(
    session, *, now: datetime | None = None, ttl_days: int | None = None
) -> int:
    """把超时未确认的候选翻成 `expired`。**不删字节、不删配方。**

    幂等：已 `expired` 的行不在查询范围里。返回本次翻了多少条。
    """
    days = settings.archive_candidate_ttl_days if ttl_days is None else ttl_days
    if days <= 0:
        return 0
    rows = (
        await session.scalars(
            select(Asset).where(
                Asset.stage == "candidate",
                Asset.source == AssetSource.ARCHIVE,
                Asset.deleted_at.is_(None),
            )
        )
    ).all()
    flipped = [row for row in rows if is_expired(row.created_at, days, now=now)]
    for row in flipped:
        row.stage = "expired"
    if flipped:
        await session.commit()
        logger.info("候选过期：%d 条", len(flipped))
    return len(flipped)


def _summary(recipe: GenerationRecipe | None, count: int, sample: Asset) -> str:
    """横幅上那一行："模型 · 4 张" 或 "4 张 · 1024×1024"。"""
    size = f"{sample.width}×{sample.height}" if sample.width and sample.height else sample.type
    head = recipe.model if recipe is not None and recipe.model else sample.type
    return f"{head} · {count} 张 · {size}"
