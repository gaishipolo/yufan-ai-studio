"""配方：写入（只有插入）、血缘查询、一键衍生的输入回填。

## 三条不变量

1. **配方只增不改**。这里没有 `update` 方法，路由层也没有 PUT/PATCH
   （`test_asset_recipes.py` 扫路由表来保证）。需要修正时补录新配方，
   旧的保留——否则"当时用了什么模型、写了什么提示词"可以被事后改写。
2. **参考图同时写两处**：`reference_asset_ids`（冻结的输入列表）与
   `asset_refs`（可反查的行）。写的时候一次写全，读的时候各取所需。
3. **衍生不改原素材**。三个入口只回填输入位，不新建素材、不改原记录；
   真正的产物要等生成完成、作为候选入库。原地改会让血缘断开、让引用方
   看到的内容变化、让"这条被谁用过"的答案失真。

## 血缘是图，不是树

`parents`（怎么来的）/ `children`（同样的输入还生出了什么）/ `used_by`
（谁在用它）三段分开返回。合成一棵树会暗示"有唯一根"，而实际上同一张图
可以被 20 次生成同时引用，那 20 个结果之间没有任何关系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import BadRequest, Forbidden
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.archive_service import ArchiveAssetService
from modules.asset.errors import NotGenerated
from modules.asset.models import Asset
from modules.asset.recipe_models import AssetRef, GenerationRecipe
from modules.asset.recipe_schema import (
    AssetRefOut,
    AssetUsage,
    DeriveIntent,
    DeriveMode,
    LineageNode,
    LineageOut,
    RecipeOut,
)
from modules.asset.subject_grants import allows, published_grants
from modules.asset.subject_models import Subject, SubjectMember

__all__ = ["RecipeDraft", "RecipeService", "create_recipe"]

#: 血缘向上/向下最多走几层。衍生链在真实使用里很少有超过三层的，
#: 而一个环（数据坏了）会让"一直向上"变成死循环——上限是防坏数据，不是防用户。
_MAX_DEPTH = 5

#: 「谁在用它」最多列几条。这个列表是给人看的证据，不是审计日志；
#: 真有 200 个引用时，详情浮层也放不下，给前 20 条 + 一句"还有更多"更诚实。
_USED_BY_LIMIT = 20

#: 提示词摘要长度（横幅上一行就够）
_PROMPT_SUMMARY = 60


@dataclass(frozen=True, slots=True)
class RecipeDraft:
    """新建配方所需的全部字段。缺字段用默认值，**不校验"完整性"**。

    完整性（生成型素材必须配方齐全）是**提交到团队库**时的前置条件，不是入库条件：
    刚生成出来的东西可能是草稿、可能是试跑，入库就拦会让失败信息出现在
    用户看不懂的地方（生成已完成，却报"配方不完整"）。
    """

    model: str = ""
    model_version: str = ""
    provider: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    prompt_language: str = "zh"
    params: dict[str, Any] = field(default_factory=dict)
    seed: str = ""
    seed_requested: str = ""
    task_id: str = ""
    template_id: str = ""
    template_version: int = 0
    subject_version: int = 0
    content_label: dict[str, Any] = field(default_factory=dict)
    parent_recipe_id: str | None = None
    #: `(asset_id, role)` 列表，顺序即 `sort_order`
    references: tuple[tuple[str, str], ...] = ()
    cost_units: float = 0.0
    created_by: str = ""


async def create_recipe(session: AsyncSession, draft: RecipeDraft) -> GenerationRecipe:
    """插入一条配方（连带参考图关联）。**不 commit**：与调用方同一事务。

    参考图列表同时写进 JSON 与关联表，且**用同一个元组**：分开构造时
    两份数据会在"某次只改了一处"之后开始不一致，而症状是"血缘页少了张图"。
    """
    referenced = [asset_id for asset_id, _ in draft.references]
    recipe = GenerationRecipe(
        model=draft.model[:64],
        model_version=draft.model_version[:64],
        provider=draft.provider[:64],
        prompt=draft.prompt,
        negative_prompt=draft.negative_prompt,
        prompt_language=draft.prompt_language[:8],
        params=dict(draft.params),
        seed=draft.seed[:64],
        seed_requested=draft.seed_requested[:64],
        task_id=draft.task_id[:32],
        template_id=draft.template_id[:32],
        template_version=draft.template_version,
        subject_version=draft.subject_version,
        content_label=dict(draft.content_label),
        parent_recipe_id=draft.parent_recipe_id,
        reference_asset_ids=referenced,
        cost_units=draft.cost_units,
        created_by=draft.created_by[:32],
        created_at=datetime.now(UTC),
    )
    session.add(recipe)
    await session.flush()
    session.add_all(
        [
            AssetRef(
                recipe_id=recipe.id,
                asset_id=asset_id,
                role=role or "reference",
                sort_order=index,
            )
            for index, (asset_id, role) in enumerate(draft.references)
        ]
    )
    await session.flush()
    return recipe


class RecipeService:
    """血缘与衍生。可见性交给 `ArchiveAssetService.visible()`，不在这里重写一遍。"""

    def __init__(self, session: AsyncSession, archive: ArchiveAssetService) -> None:
        self.session = session
        self.archive = archive
        self.repo = ArchiveAssetRepository(session)

    # ------------------------------------------------------------------ 血缘
    async def lineage(self, asset_id: str, owner: str) -> LineageOut:
        row = await self.archive.visible(asset_id, owner)
        recipe = await self._recipe(row.recipe_id)
        if recipe is None:
            # 上传件：血缘为空是**事实**，不是错误。前端据此隐藏这一段
            return LineageOut(asset_id=row.id, used_by=await self._used_by(row))
        return LineageOut(
            asset_id=row.id,
            recipe=_out(recipe),
            references=await self._references(recipe.id),
            parents=await self._parents(recipe),
            children=await self._children(recipe),
            used_by=await self._used_by(row),
        )

    async def _recipe(self, recipe_id: str | None) -> GenerationRecipe | None:
        if not recipe_id:
            return None
        return await self.session.get(GenerationRecipe, recipe_id)

    async def _references(self, recipe_id: str) -> list[AssetRefOut]:
        rows = (
            await self.session.execute(
                select(AssetRef, Asset)
                .join(Asset, Asset.id == AssetRef.asset_id)
                .where(AssetRef.recipe_id == recipe_id)
                .order_by(AssetRef.sort_order, AssetRef.asset_id)
            )
        ).all()
        return [
            AssetRefOut(
                asset_id=ref.asset_id,
                role=ref.role,
                sort_order=ref.sort_order,
                name=asset.name,
                preview_url=_preview_of(asset),
            )
            for ref, asset in rows
        ]

    async def _produced(self, recipe_id: str) -> Asset | None:
        """这条配方产出的素材（取一条代表）。

        一条配方可能产出多张（一次生成 4 格），血缘链上只画一张代表：
        把 4 张全塞进链里会让"这是一条链"变成"这是一棵树"。
        """
        return await self.session.scalar(
            select(Asset)
            .where(Asset.recipe_id == recipe_id, Asset.deleted_at.is_(None))
            .order_by(Asset.created_at)
            .limit(1)
        )

    async def _node(self, recipe: GenerationRecipe) -> LineageNode:
        asset = await self._produced(recipe.id)
        return LineageNode(
            recipe=_out(recipe),
            asset_id=asset.id if asset is not None else "",
            name=asset.name if asset is not None else "",
            preview_url=_preview_of(asset) if asset is not None else None,
        )

    async def _parents(self, recipe: GenerationRecipe) -> list[LineageNode]:
        nodes: list[LineageNode] = []
        cursor = recipe.parent_recipe_id
        while cursor and len(nodes) < _MAX_DEPTH:
            parent = await self.session.get(GenerationRecipe, cursor)
            if parent is None or parent.id == recipe.id:
                break
            nodes.append(await self._node(parent))
            cursor = parent.parent_recipe_id
        return nodes

    async def _children(self, recipe: GenerationRecipe) -> list[LineageNode]:
        rows = (
            await self.session.scalars(
                select(GenerationRecipe)
                .where(GenerationRecipe.parent_recipe_id == recipe.id)
                .order_by(GenerationRecipe.created_at.desc())
                .limit(_MAX_DEPTH)
            )
        ).all()
        return [await self._node(row) for row in rows]

    async def _used_by(self, row: Asset) -> list[AssetUsage]:
        """谁在用它：别的配方引用了它（`asset`）+ 主体把它当成员（`subject`）。"""
        recipe_ids = (
            await self.session.scalars(
                select(AssetRef.recipe_id).where(AssetRef.asset_id == row.id).limit(_USED_BY_LIMIT)
            )
        ).all()
        usages: list[AssetUsage] = []
        if recipe_ids:
            # 引用它的配方产出的素材 = "用这张图生成的图"。排除自己：
            # 自引用是数据错误，但展示成"自己用自己"只会让人以为界面坏了
            derived = (
                await self.session.scalars(
                    select(Asset)
                    .where(Asset.recipe_id.in_(list(recipe_ids)), Asset.id != row.id)
                    .order_by(Asset.created_at.desc())
                    .limit(_USED_BY_LIMIT)
                )
            ).all()
            usages += [AssetUsage(kind="asset", id=item.id, name=item.name) for item in derived]
        subjects = (
            await self.session.execute(
                select(Subject)
                .join(SubjectMember, SubjectMember.subject_id == Subject.id)
                .where(SubjectMember.asset_id == row.id, Subject.archived_at.is_(None))
                .order_by(Subject.updated_at.desc())
                .limit(_USED_BY_LIMIT)
            )
        ).scalars().all()
        usages += [AssetUsage(kind="subject", id=item.id, name=item.name) for item in subjects]
        return usages

    # ------------------------------------------------------------------ 衍生
    async def derive(self, asset_id: str, owner: str, mode: DeriveMode) -> DeriveIntent:
        """回填"照这个素材再生成一次"所需的输入。**不落库、不建任务**。

        不建任务的理由是计费：生成是按次扣费的，把"点了衍生"顺手变成一次提交，
        等于让一个浏览动作产生费用。这里只把输入位填好，提交由生成页自己做。
        """
        row = await self.archive.visible(asset_id, owner)
        grants = await published_grants(self.session, [row.id])
        if not allows(grants, row.id, "derive"):
            # 逐成员判（木桶不压整体）：这张图在它所属的已发布主体里没开放衍生
            raise Forbidden("这张素材在所属主体里未开放衍生")

        if mode == "prompt_extract":
            recipe = await self._recipe(row.recipe_id)
            if recipe is None:
                raise NotGenerated()
            return DeriveIntent(
                mode=mode,
                asset_id=row.id,
                prompt=recipe.prompt,
                source_recipe_id=recipe.id,
            )

        if row.type != "image":
            # 首帧与图生图参考都要求"图片位"。视频走"先截一帧"的路径，
            # 而不是在这里隐式抽帧——抽帧会产生新素材，那是另一条写路径
            raise BadRequest(
                "首帧与图生图参考需要一个图片位的素材；视频请先截一帧再上传"
            )
        return DeriveIntent(
            mode=mode,
            asset_id=row.id,
            reference_asset_ids=[row.id],
            reference_role="first_frame" if mode == "video_first_frame" else "reference",
            source_recipe_id=row.recipe_id,
        )


def _out(recipe: GenerationRecipe) -> RecipeOut:
    return RecipeOut(
        id=recipe.id,
        model=recipe.model,
        model_version=recipe.model_version,
        provider=recipe.provider,
        prompt=recipe.prompt,
        negative_prompt=recipe.negative_prompt,
        prompt_language=recipe.prompt_language,
        params=dict(recipe.params or {}),
        seed=recipe.seed,
        seed_requested=recipe.seed_requested,
        task_id=recipe.task_id,
        template_id=recipe.template_id,
        template_version=recipe.template_version,
        subject_version=recipe.subject_version,
        content_label=dict(recipe.content_label or {}),
        parent_recipe_id=recipe.parent_recipe_id,
        reference_asset_ids=list(recipe.reference_asset_ids or []),
        cost_units=recipe.cost_units,
        created_by=recipe.created_by,
        created_at=recipe.created_at,
    )


def _preview_of(asset: Asset) -> str | None:
    """缩略图地址。与 `ArchiveAssetRepository.to_media` 同一口径：有封面才给。"""
    if not (asset.cover_key or asset.cover_url):
        return None
    return f"{settings.api_prefix}/assets/{asset.id}/thumbnail"


def summarize(prompt: str) -> str:
    """横幅上的一行提示词。空提示词回空串（不是省略号）。"""
    text = " ".join(prompt.split())
    if not text:
        return ""
    return text if len(text) <= _PROMPT_SUMMARY else text[:_PROMPT_SUMMARY] + "…"
