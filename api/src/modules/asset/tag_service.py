"""标签与保存视图的服务层：词表初始化、贴标签、筛选解析、视图存取。

## 三条不变量

1. **接口只接受 `tag_id`**，不接受词。收到词就意味着有人在前端拼字符串，
   而拼出来的词三个月后一定和词表里的对不上（"正面图" / "正面" / "正视图"）。
2. **标签不物理删除**，只 `retired`；合并留 `merged_into`，筛选时一跳解析。
3. **`AND` 语义**：多个 `tagIds` 是「同时具备」，不是「任一」。这是筛选条上
   「极简留白 + 俯拍 + 无人」的直译——用户要的是同时满足三者的那几张图。

## 为什么 usage 是算出来的

见 `tag_models.py` 的说明：存计数会在软删、合并、批量改写三条路径上与真实
引用数漂移，而漂移没有症状。这里每次 `GROUP BY` 算，代价是索引扫描。

**注意「在用」的口径**：只数未删除的素材。回收站里的关联仍然保留（恢复后
标签要回来），但不计入筛选条的热度——否则一个词可以靠一堆删除的素材
把自己顶进常显区。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import utcnow
from modules.asset.errors import (
    TagExists,
    TagUnknown,
    ViewNameExists,
    ViewNotFound,
)
from modules.asset.models import Asset
from modules.asset.tag_models import AssetTag, AssetView, Tag
from modules.asset.tag_vocabulary import DIMENSION_LABELS, DIMENSIONS, INITIAL_TAGS

__all__ = [
    "PINNED_THRESHOLD",
    "TagService",
    "ensure_vocabulary",
    "normalize_name",
]

#: 用满这么多次才进筛选条常显区。5 这个数来自设计文档 §3.3：
#: 低于它说明这个词还没被真的用起来，占用固定宽度的界面没有收益。
PINNED_THRESHOLD: Final[int] = 5


def normalize_name(name: str) -> str:
    """校验并规整词。全角空格、首尾空白、连续空格都收敛成一个半角空格。

    不一致的空白会让「3D  渲染」和「3D 渲染」变成两个词，而它们在界面上
    长得一模一样——这类重复在词表里最难发现。
    """
    return " ".join(name.split())


async def ensure_vocabulary(session: AsyncSession, *, commit: bool = False) -> int:
    """把初始词表补齐，返回新插入的条数。幂等，可反复调用。

    和分类树一样需要「幂等初始化」而不是「迁移里建一次」：`app seed --reset`
    按 metadata 清表，清完词表就空了；生产库升级时也需要补上新增的词。

    冲突用 savepoint 兜住：并发两处同时补词表时，一个会撞唯一索引，
    回滚 savepoint 后重查即可——**不能回滚外层事务**，那会把调用方
    （seed / bootstrap）已经写好的东西一起扔掉。
    """
    existing = set((await session.execute(select(Tag.dimension, Tag.name))).all())
    missing = [
        (dimension, name) for dimension, name in INITIAL_TAGS if (dimension, name) not in existing
    ]
    if not missing:
        return 0
    rows = [
        Tag(dimension=dimension, name=name, status="active", source="manual", created_by="system")
        for dimension, name in missing
    ]
    try:
        async with session.begin_nested():
            session.add_all(rows)
            await session.flush()
    except IntegrityError:
        # 并发补齐：对方已经插了其中一部分。不重试整体，直接返回 0——
        # 下一次启动会把仍缺的补上，而"这次补了几个"不是需要精确的数字。
        return 0
    if commit:
        await session.commit()
    return len(rows)


def _expand_merged(rows: Iterable[Tag]) -> dict[str, str]:
    """`{被合并掉的 id: 合并去向 id}`。只解析一跳，够用且不会成环。"""
    return {row.id: row.merged_into for row in rows if row.merged_into}


class TagService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 词表
    async def catalog(self) -> list[tuple[str, list[tuple[Tag, int]]]]:
        """按维度分组返回 `[(维度码, [(词, 在用素材数)])]`。

        排序：常显词（`use_count >= PINNED_THRESHOLD`）在前，同组内按词名，
        保证筛选条的**顺序稳定**——顺序随热度变化会让用户每次都要重新找。
        """
        counts = dict(
            (
                await self.session.execute(
                    select(AssetTag.tag_id, func.count())
                    .join(Asset, Asset.id == AssetTag.asset_id)
                    .where(Asset.deleted_at.is_(None))
                    .group_by(AssetTag.tag_id)
                )
            ).all()
        )
        # 已合并的词不出现在词表里：它们的存在只为解释历史 id
        rows = list(
            (
                await self.session.scalars(
                    select(Tag)
                    .where(Tag.status == "active", Tag.merged_into == "")
                    .order_by(Tag.dimension, Tag.name)
                )
            ).all()
        )
        by_dimension: dict[str, list[tuple[Tag, int]]] = {}
        for row in rows:
            by_dimension.setdefault(row.dimension, []).append((row, int(counts.get(row.id, 0))))
        ordered: list[tuple[str, list[tuple[Tag, int]]]] = []
        for dimension in DIMENSIONS:
            items = by_dimension.pop(dimension, [])
            items.sort(key=lambda pair: (pair[1] < PINNED_THRESHOLD, pair[0].name))
            ordered.append((dimension, items))
        # 词表之外新增的维度（管理员自己加的词）排在最后，不丢
        for dimension, items in by_dimension.items():
            ordered.append((dimension, items))
        return ordered

    async def create(self, dimension: str, name: str, actor_id: str) -> Tag:
        """新增一个词。`dimension` 必须在词表的六个维度内。"""
        if dimension not in DIMENSIONS:
            raise TagUnknown(details={"dimension": dimension, "allowed": list(DIMENSIONS)})
        clean = normalize_name(name)
        if not clean:
            raise TagUnknown(details={"name": name})
        existing = await self.session.scalar(select(Tag).where(Tag.name == clean))
        if existing is not None:
            raise TagExists(details={"name": clean, "id": existing.id, "status": existing.status})
        row = Tag(
            dimension=dimension,
            name=clean,
            status="active",
            source="manual",
            created_by=actor_id,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:  # 并发同名词
            await self.session.rollback()
            raise TagExists(details={"name": clean}) from exc
        await self.session.commit()
        return row

    async def resolve(self, tag_ids: Sequence[str]) -> list[str]:
        """把外部传入的 id 列表解析成"当下有效"的 id 列表。

        - 未知 id → `TagUnknown`（422），不静默丢弃：静默丢弃会让筛选结果
          比用户以为的更宽，而界面上看不出少了一个条件；
        - 已合并 → 一跳跳到新 id，去重；
        - 已归档 → 保留。归档的含义是「不再进待选列表」，不是「历史上没打过」，
          按归档词筛选历史素材仍然要能筛出东西。
        """
        wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
        if not wanted:
            return []
        rows = list((await self.session.scalars(select(Tag).where(Tag.id.in_(wanted)))).all())
        found = {row.id for row in rows}
        missing = [tag_id for tag_id in wanted if tag_id not in found]
        if missing:
            raise TagUnknown(details={"tagIds": missing})
        redirect = _expand_merged(rows)
        resolved: list[str] = []
        for tag_id in wanted:
            target = redirect.get(tag_id, tag_id)
            # 一跳之后仍指向被合并的词时停在原地：成环是数据问题，不该让查询 500
            if target not in resolved:
                resolved.append(target)
        return resolved

    # ------------------------------------------------------------------ 素材
    async def for_assets(self, asset_ids: Sequence[str]) -> dict[str, list[Tag]]:
        """批量取素材的标签，按素材分组。列表页一次查完，别在循环里查。"""
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not wanted:
            return {}
        rows = (
            await self.session.execute(
                select(AssetTag.asset_id, Tag)
                .join(Tag, Tag.id == AssetTag.tag_id)
                .where(AssetTag.asset_id.in_(wanted))
                .order_by(Tag.dimension, Tag.name)
            )
        ).all()
        grouped: dict[str, list[Tag]] = {asset_id: [] for asset_id in wanted}
        for asset_id, tag in rows:
            grouped[asset_id].append(tag)
        return grouped

    async def set_asset_tags(
        self, asset_id: str, tag_ids: Sequence[str], actor_id: str
    ) -> list[Tag]:
        """整体替换一条素材的标签集合。

        用「整体替换」而不是「逐条增删」：前端做的是勾选面板，它天然知道自己
        勾了哪几个；改成增删 API 后，两个页面各发一半请求就会互相覆盖。
        """
        resolved = await self.resolve(tag_ids)
        current = {
            row.tag_id
            for row in (
                await self.session.scalars(select(AssetTag).where(AssetTag.asset_id == asset_id))
            ).all()
        }
        add = [tag_id for tag_id in resolved if tag_id not in current]
        remove = [tag_id for tag_id in current if tag_id not in resolved]
        for tag_id in add:
            self.session.add(AssetTag(asset_id=asset_id, tag_id=tag_id, added_by=actor_id))
        if remove:
            for row in (
                await self.session.scalars(
                    select(AssetTag).where(
                        AssetTag.asset_id == asset_id, AssetTag.tag_id.in_(remove)
                    )
                )
            ).all():
                await self.session.delete(row)
        if add:
            # 只在新打上时刷新：`last_used_at` 是「最后一次被使用」的时间。
            # 取消再打上也算一次使用；但重复提交同一集合不该刷新它，
            # 否则一个从不使用的词只要有人反复保存就能逃过「90 天零使用」的复查。
            stamped = utcnow()
            for tag in (await self.session.scalars(select(Tag).where(Tag.id.in_(add)))).all():
                tag.last_used_at = stamped
        await self.session.commit()
        return list((await self.session.scalars(select(Tag).where(Tag.id.in_(resolved)))).all())

    # ------------------------------------------------------------------ 视图
    async def list_views(self, owner_id: str) -> list[AssetView]:
        return list(
            (
                await self.session.scalars(
                    select(AssetView)
                    .where(AssetView.owner_id == owner_id)
                    .order_by(AssetView.created_at)
                )
            ).all()
        )

    async def save_view(self, owner_id: str, name: str, query: dict, sort: str) -> AssetView:
        clean = normalize_name(name)
        if not clean:
            raise ViewNameExists(details={"name": name})
        if await self.session.scalar(
            select(AssetView.id).where(AssetView.owner_id == owner_id, AssetView.name == clean)
        ):
            raise ViewNameExists(details={"name": clean})
        row = AssetView(
            owner_id=owner_id,
            name=clean,
            # `ensure_ascii=False` 存原文：这份 JSON 会被人工看过（排查"视图恢复了
            # 什么条件"时），转义过的中文读不了
            query_json=json.dumps(query, ensure_ascii=False, sort_keys=True),
            sort=sort,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.commit()
        return row

    async def delete_view(self, owner_id: str, view_id: str) -> None:
        row = await self.session.scalar(
            select(AssetView).where(AssetView.id == view_id, AssetView.owner_id == owner_id)
        )
        if row is None:
            raise ViewNotFound()
        await self.session.delete(row)
        await self.session.commit()

    async def view_query(self, row: AssetView) -> dict:
        """视图存的是字符串，出参要还原成对象。坏 JSON 不该让整个列表 500。"""
        try:
            value = json.loads(row.query_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


def dimension_label(dimension: str) -> str:
    """维度码 → 中文分组标题。未知维度原样返回，不抛错（管理员可能加了新维度）。"""
    return DIMENSION_LABELS.get(dimension, dimension)
