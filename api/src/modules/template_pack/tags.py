"""模板**独立**的两级标签词表（维度 / 取值）与筛选口径。

## 为什么独立而不复用素材库词表

素材库的「产品 / 画面类型 / 角色」是**素材的分类轴**；模板的分类轴是另一回事
（按用途、按业务线、按片子类型……）。共用一套词表会让两个领域互相污染分类维度，
而模板封装页要能自己管理词表（用户拍板）。所以这里另起 `template_tags` 表。

## 两级口径与筛选语义

与素材库同口径（`library/tags.py`）：`parent_id == ""` 是一级（维度，只分组），
二级是取值（能打在模板上）。选中标签按一级维度分组：

- **同维度多选 = OR**（选两个产品 = 两个都要看）；
- **跨维度 = AND**（选产品 + 用途 = 既是这个产品又是这个用途）。

两处实现是刻意的重复（模块间禁止互相 import）：口径必须一致，用守卫测试钉住。

## 标签存在哪

`template_specs.tag_ids`（head 的 JSON 列），**不进 draft / 不进发布快照**：
标签是分类元信息，改了立刻生效，不该要求发版，也不该让历史任务的快照跟着变。
删词表项时同步从所有模板的 tag_ids 里移除。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from modules.template_pack.models import TemplateSpec, TemplateTag
from modules.template_pack.spec_schema import TagIn, TagOut, TagParentIn

#: 与素材库一致：最多两级（维度 / 取值）。
MAX_TAG_DEPTH = 2


async def all_tags(session: AsyncSession) -> list[TemplateTag]:
    rows = (await session.scalars(select_tags_ordered())).all()
    return list(rows)


def select_tags_ordered() -> sa.Select:
    return sa.select(TemplateTag).order_by(TemplateTag.created_at, TemplateTag.id)


def tree_index(rows: list[TemplateTag]) -> tuple[dict[str, str], dict[str, int], dict[str, str]]:
    """(parent_id, depth, 祖先整条链) 三张索引，一次算完。"""
    by_id = {row.id: row for row in rows}
    parent: dict[str, str] = {}
    depth: dict[str, int] = {}
    path: dict[str, str] = {}

    def visit(row: TemplateTag) -> tuple[int, str]:
        if row.id in depth:
            return depth[row.id], path.get(row.id, "")
        pid = row.parent_id or ""
        if not pid or pid not in by_id:
            parent[row.id] = ""
            depth[row.id] = 1
            path[row.id] = ""
            return 1, ""
        p_depth, p_path = visit(by_id[pid])
        parent[row.id] = pid
        depth[row.id] = p_depth + 1
        path[row.id] = f"{p_path} / {by_id[pid].name}" if p_path else by_id[pid].name
        return depth[row.id], path[row.id]

    for row in rows:
        visit(row)
    return parent, depth, path


def children_of(rows: list[TemplateTag]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in rows:
        if row.parent_id:
            out.setdefault(row.parent_id, []).append(row.id)
    return out


def descendants(tag_id: str, children: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    stack = list(children.get(tag_id, ()))
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(children.get(current, ()))
    return out


def tag_out(row: TemplateTag, parent_name: str | None, depth: int, spec_count: int = 0) -> TagOut:
    return TagOut(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id or None,
        parent_name=parent_name or None,
        depth=depth,
        spec_count=spec_count,
    )


def group_by_dimension(rows: list[TemplateTag], tag_ids: list[str]) -> list[list[str]]:
    """选中的标签按一级维度分组；组内 = 自身 + 全部下级（OR），组间 = AND。"""
    parent, _depth, _path = tree_index(rows)
    children = children_of(rows)
    groups: dict[str, set[str]] = {}
    for tag_id in tag_ids:
        dimension = tag_id if not parent.get(tag_id) else parent[tag_id]
        expanded = groups.setdefault(dimension, set())
        expanded.add(tag_id)
        expanded.update(descendants(tag_id, children))
    return [sorted(group) for group in groups.values()]


def spec_matches_groups(row: TemplateSpec, groups: list[list[str]]) -> bool:
    """一个模板要在**每个**维度里都命中（AND），维度内命中任意一个即可（OR）。"""
    tagged = set(row.tag_ids or ())
    return all(bool(tagged & set(group)) for group in groups)


class TagService:
    """词表 CRUD：与素材库同纪律（同父同名幂等/冲突、两级上限、删父提子）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tags(self) -> list[TagOut]:
        rows = await all_tags(self.session)
        _parent, depth, path = tree_index(rows)
        specs = (await self.session.scalars(sa.select(TemplateSpec))).all()
        counts: dict[str, int] = {row.id: 0 for row in rows}
        children = children_of(rows)
        # 一个标签的数字 = **挂了它自己或它下级的模板数**（按模板去重）。
        # 方向不能反：模板打的是二级取值，一级维度要把它下面所有取值的模板
        # 归拢到自己身上，否则维度行永远显示 0、点进去却有结果。
        # 与素材库 `TagService.list_tags` 的 counts 归并同一口径。
        direct: dict[str, set[str]] = {}
        for spec in specs:
            for tag_id in spec.tag_ids or ():
                direct.setdefault(tag_id, set()).add(spec.id)
        for row in rows:
            related = [row.id, *descendants(row.id, children)]
            hit_specs: set[str] = set()
            for item in related:
                hit_specs.update(direct.get(item, ()))
            counts[row.id] = len(hit_specs)
        return [
            # `path[row.id]` 才是**这条标签自己**的父链；`path[row.parent_id]`
            # 是它父亲的父链（一级标签的父链是空串），会把二级的 parentName 弄丢。
            tag_out(row, path.get(row.id) or None, depth[row.id], counts[row.id])
            for row in rows
        ]

    async def create(self, payload: TagIn, actor: CurrentUser) -> TagOut:
        name = payload.name.strip()
        parent_id = payload.parent_id or ""
        rows = await all_tags(self.session)
        _parent, depth, path = tree_index(rows)
        parent_name: str | None = None
        if parent_id:
            if parent_id not in {row.id for row in rows}:
                raise BadRequest("父标签不存在")
            if depth.get(parent_id, 1) >= MAX_TAG_DEPTH:
                raise BadRequest(
                    f"标签最多 {MAX_TAG_DEPTH} 级：一级是维度（分组），取值只能挂在维度下面"
                )
            # 两级下父必是一级：`path[parent_id]` 是它的祖先链（空串），
            # 父链显示名就是父自己的名字。
            parent_name = path.get(parent_id) or next(
                item.name for item in rows if item.id == parent_id
            )
        existing = next(
            (row for row in rows if row.name == name and row.parent_id == parent_id), None
        )
        if existing is not None:
            return tag_out(existing, parent_name, depth[existing.id])
        row = TemplateTag(name=name, parent_id=parent_id, created_by=actor.id)
        self.session.add(row)
        await self.session.commit()
        return tag_out(row, parent_name, depth.get(parent_id, 1) + 1 if parent_id else 1)

    async def rename(self, tag_id: str, payload: TagIn) -> TagOut:
        row = await self.session.get(TemplateTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        rows = await all_tags(self.session)
        _parent, depth, path = tree_index(rows)
        name = payload.name.strip()
        clash = next(
            (
                other
                for other in rows
                if other.name == name and other.parent_id == row.parent_id and other.id != row.id
            ),
            None,
        )
        if clash is not None:
            raise Conflict("同一分组下已有同名标签")
        row.name = name
        await self.session.commit()
        return tag_out(row, path.get(row.id) or None, depth[row.id])

    async def move(self, tag_id: str, payload: TagParentIn) -> TagOut:
        row = await self.session.get(TemplateTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        parent_id = payload.parent_id or ""
        if parent_id == tag_id:
            raise BadRequest("标签不能挂到自己下面")
        rows = await all_tags(self.session)
        parent, depth, path = tree_index(rows)
        children = children_of(rows)
        if parent_id:
            if parent_id not in {item.id for item in rows}:
                raise BadRequest("父标签不存在")
            if parent_id in descendants(tag_id, children):
                raise BadRequest("标签不能挂到自己的下级下面")
            own_depth = depth.get(tag_id, 1)
            descendants_ids = descendants(tag_id, children)
            # 叶子标签（二级取值，没有下级）的 descendants 是空的 —— max() 对空
            # 序列抛 ValueError，之前把"移动叶子到别的维度"全打成了 500。
            # 只有自己时子树高度就是 1。
            subtree_max = (
                max(depth.get(item, own_depth) for item in descendants_ids)
                if descendants_ids
                else own_depth
            )
            height = subtree_max - own_depth + 1
            if depth.get(parent_id, 1) + height > MAX_TAG_DEPTH:
                raise BadRequest(f"标签最多 {MAX_TAG_DEPTH} 级：一级是维度，取值只能挂在维度下面")
        clash = next(
            (
                other
                for other in rows
                if other.name == row.name and other.parent_id == parent_id and other.id != row.id
            ),
            None,
        )
        if clash is not None:
            raise Conflict("目标分组下已有同名标签，请先改名")
        row.parent_id = parent_id
        await self.session.commit()
        parent_name = (
            path.get(parent_id)
            or next((item.name for item in rows if item.id == parent_id), None)
            if parent_id
            else None
        )
        return tag_out(
            row,
            parent_name,
            depth.get(parent_id, 1) + 1 if parent_id else 1,
        )

    async def delete(self, tag_id: str) -> None:
        row = await self.session.get(TemplateTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        rows = await all_tags(self.session)
        children = children_of(rows)
        kids = children.get(tag_id, [])
        target_parent = row.parent_id or ""
        sibling_names = {
            other.name for other in rows if other.parent_id == target_parent and other.id != tag_id
        }
        clash = sorted(
            {next((r.name for r in rows if r.id == kid), "") for kid in kids} & sibling_names
        )
        if clash:
            raise Conflict(f"子标签与同层已有标签同名，请先改名：{', '.join(clash[:5])}")
        if kids:
            await self.session.execute(
                sa.update(TemplateTag)
                .where(TemplateTag.parent_id == tag_id)
                .values(parent_id=target_parent)
            )
        # 只清被删行自己的 id。它的下级会提升一级但 id 不变、仍可打标，
        # 模板身上挂的那些下级标签要留着（与 library 的 delete 同口径）。
        specs = (await self.session.scalars(sa.select(TemplateSpec))).all()
        for spec in specs:
            kept = [item for item in (spec.tag_ids or ()) if item != tag_id]
            if len(kept) != len(spec.tag_ids or ()):
                spec.tag_ids = kept
        await self.session.delete(row)
        await self.session.commit()
