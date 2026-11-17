"""标签树从三级压成两级：三级取值合并进「维度」，词表只留两层

Revision ID: c2b7d4e91a06
Revises: d9e4f1a83c07
Create Date: 2026-11-17 10:00:00.000000

## 为什么改

三级模型（维度 / 系列 / 具体词）把**跨实例复用的词**塞进了某个具体实例的深处：

    产品
      ├ 6036
      │   ├ 正面图      ← 只有 6036 有这三个词
      │   ├ 三视图
      │   └ 细节图
      ├ T35           ← 空着
      └ 3030          ← 空着

「正面图 / 三视图 / 细节图」是**画面类型**，与具体哪个产品无关（正交维度）。
挂在 6036 下面的后果是：每新增一个产品就要把整套词复制一遍（3N 个标签），
而且模板封装想表达「这个插槽必须是任意产品的正面图」根本做不到 ——
插槽的候选集是标签 id，「T35 的正面图」是另一个 id，圈不进来。

两级模型（维度 / 取值）让画面类型自己成为一条轴：

    产品（维度）        画面类型（维度）
      ├ 6036              ├ 正面图
      ├ T35               ├ 三视图
      └ 3030              ├ 细节图

标签数从 3N 降到 N+3，且「必须是任意产品的正面图」变成圈**一个** id。

## 迁移做什么

三级词在别的维度下**常常已经有同名的二级词**（人手建过一次「产品图类型」
维度时就是这么来的），于是同一个词有了两个 id。迁移分两种情况：

1. **有同名副本 → 合并**：保留一个 id，另一个的引用全部转过去，删掉多余的。
   保留谁看**谁被引用得更多**（模板槽位引用优先，其次资产绑定数）：
   模板槽位的 `tag_ids` 是对外契约，改它等于改已发布模板的语义，
   所以被模板引用的那个 id 必须活下来。
2. **没有同名副本 → 提升**：直接提到祖父下（成为二级取值），
   保证词表不残留第三层。tag_id 不变，资产绑定与槽位引用都不用动。

## 为什么保留旧 id 而不是重建词

「产品图类型」维度下的「正面图」与 6036 下的「正面图」是**同一个词**。
合并时若把旧三级词删掉、留新维度那份，模板槽位里指向旧 id 的 `tag_ids`
就得改 —— 那是已发布模板的契约。宁可保留被引用多的那个 id，
把另一份的绑定转过去。

## 打在「维度」上的绑定为什么不删

存量里有一批素材直接打在了一级标签上（如「角色」），在旧的三级模型里
一级是系列、是能打的词。新模型下一级成了分组，界面不再允许选它，
但**存量绑定留着无害且有用**：筛选时选一级 = 该轴下全部取值，
这些素材仍然会命中（`tag_filter_clause` 展开「自身 + 全部下级」）。
删掉它们等于凭空丢掉「这些素材是角色」这条信息，不可逆。

## 降级

降级**不还原**结构变更 —— 合并掉的词无法重建回原父、提升过的词也记不清
原来挂在谁下面。这是有意的：降级路径只是「让旧代码还能跑起来」，
不承诺数据无损。

降级后词表仍是两层结构，在旧的三级筛选口径（全 OR）下命中范围只会更宽，
不会更窄。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2b7d4e91a06"
down_revision: str | None = "d9e4f1a83c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 迁移用到的表（不 import 应用模型 —— 模型的定义会变，迁移必须自足）。
_tags = sa.table(
    "library_tags",
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("parent_id", sa.String),
)
_asset_tags = sa.table(
    "library_asset_tags",
    sa.column("id", sa.String),
    sa.column("asset_id", sa.String),
    sa.column("tag_id", sa.String),
)
_slots = sa.table(
    "template_spec_slots",
    sa.column("id", sa.String),
    sa.column("tag_id", sa.String),
    sa.column("tag_ids", sa.JSON),
)


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.select(_tags.c.id, _tags.c.name, _tags.c.parent_id)
    ).fetchall()
    if not rows:
        return

    by_id = {row.id: row for row in rows}

    # ---- 处理每个三级词 ---------------------------------------------------
    for row in rows:
        parent = by_id.get(row.parent_id)
        if parent is None or not parent.parent_id:
            continue  # 不是三级：一级的父是空串，二级没有父

        twin = _find_twin(row, by_id)
        if twin is None:
            # 没有同名副本：提到祖父下，成为二级取值。tag_id 不变 ——
            # 资产绑定与模板槽位的引用因此都不用动。
            _set_parent(bind, row.id, parent.parent_id)
            continue

        # 有同名副本：合并。保留被引用多的那个 id，最终挂在 twin 的父下
        # （twin 在正确的维度下，row 挂在某个具体实例下面）。
        keep, drop = (
            (row, twin)
            if _reference_score(row.id) >= _reference_score(twin.id)
            else (twin, row)
        )
        _merge(bind, keep=keep, drop=drop, target_parent=twin.parent_id)


def downgrade() -> None:
    """降级：结构变更不还原（见模块 docstring）。

    合并掉的词无法重建回原父、提升过的词也记不清原来挂在谁下面。
    降级后词表仍是两层 —— 在旧的三级筛选口径（全 OR）下命中范围只会更宽，
    不会更窄。
    """


# ---------------------------------------------------------------- 内部工具


def _find_twin(row: sa.Row, by_id: dict[str, sa.Row]) -> sa.Row | None:
    """找一个可以与 `row` 合并的同名二级词。

    候选 = 同名、且**父是一级**、且不在 `row` 自己的父分支下。

    「父是一级」是硬条件：并进一个本身是**一级**的标签会把词变成维度，
    那是另一个语义（那个一级代表一整条轴，不是这一个取值）。
    「不在同一个父下」是另一条：同一个父下同名说明是脏数据，
    合并它们对层级没帮助。

    返回**被引用得更多**的那个候选（模板槽位引用优先，其次资产绑定数，
    再其次 id 字典序）—— 保留它意味着少改一处对外契约。
    """
    candidates: list[sa.Row] = []
    for other in by_id.values():
        if other.id == row.id or other.name != row.name:
            continue
        other_parent = by_id.get(other.parent_id)
        if other_parent is None or other_parent.parent_id:
            continue  # 对方不是二级（父是空串 = 一级，或父还有父 = 三级）
        if other.parent_id == row.parent_id:
            continue  # 同一个父下 —— 那是脏数据，不是跨分支的重复
        candidates.append(other)
    if not candidates:
        return None
    return max(candidates, key=lambda c: (_reference_score(c.id), c.id))


def _reference_score(tag_id: str) -> tuple[int, int]:
    """(模板槽位引用数, 资产绑定数)。槽位引用优先 —— 它是对外契约。"""
    bind = op.get_bind()
    slot_hits = bind.execute(
        sa.select(sa.func.count()).select_from(_slots).where(_slots.c.tag_id == tag_id)
    ).scalar() or 0
    json_hits = 0
    for row in bind.execute(
        sa.select(_slots.c.tag_ids).where(_slots.c.tag_ids.is_not(None))
    ).fetchall():
        values = row.tag_ids if isinstance(row.tag_ids, list) else []
        json_hits += sum(1 for item in values if item == tag_id)
    asset_hits = bind.execute(
        sa.select(sa.func.count())
        .select_from(_asset_tags)
        .where(_asset_tags.c.tag_id == tag_id)
    ).scalar() or 0
    return (int(slot_hits) + int(json_hits), int(asset_hits))


def _merge(
    bind: sa.Connection, *, keep: sa.Row, drop: sa.Row, target_parent: str
) -> None:
    """把 `drop` 合并进 `keep`：引用转过去，`drop` 删除，`keep` 挂到 `target_parent`。

    顺序不能错：`keep` 要挂到 `target_parent` 下时，若 `target_parent` 下已经有
    一个同名的（就是 `drop`），会撞唯一索引 `uq_library_tags_name (name, parent_id)`
    —— 所以**先删 drop、再改 keep 的父**。
    """
    _move_asset_bindings(bind, drop_id=drop.id, keep_id=keep.id)
    _move_slot_refs(bind, drop_id=drop.id, keep_id=keep.id)

    bind.execute(sa.delete(_tags).where(_tags.c.id == drop.id))
    _set_parent(bind, keep.id, target_parent)


def _set_parent(bind: sa.Connection, tag_id: str, parent_id: str) -> None:
    """改父级。撞唯一索引时（目标父下已有同名）把两者合并。

    「提升孤立三级词」这一路：祖父下可能已经有一个同名的 ——
    那种情况下提升会撞 `uq_library_tags_name (name, parent_id)`。
    与其让迁移整个失败，不如合并掉重复的那条（引用转过去）。
    """
    row = bind.execute(
        sa.select(_tags.c.id, _tags.c.name, _tags.c.parent_id).where(_tags.c.id == tag_id)
    ).fetchone()
    if row is None or row.parent_id == parent_id:
        return
    clash = bind.execute(
        sa.select(_tags.c.id, _tags.c.name, _tags.c.parent_id).where(
            _tags.c.name == row.name,
            _tags.c.parent_id == parent_id,
            _tags.c.id != tag_id,
        )
    ).fetchone()
    if clash is not None:
        # 目标父下已有同名：合并，保留被引用多的那个（`clash` 已经在目标父下，
        # 通常它才是该挂在这儿的那个）。
        if _reference_score(row.id) >= _reference_score(clash.id):
            keep, drop = row, clash
        else:
            keep, drop = clash, row
        _merge(bind, keep=keep, drop=drop, target_parent=parent_id)
        return
    bind.execute(
        sa.update(_tags).where(_tags.c.id == tag_id).values(parent_id=parent_id)
    )


def _move_asset_bindings(bind: sa.Connection, *, drop_id: str, keep_id: str) -> None:
    """把 `drop_id` 的资产绑定转到 `keep_id`，目标已有则跳过（唯一索引）。"""
    existing = {
        row.asset_id
        for row in bind.execute(
            sa.select(_asset_tags.c.asset_id).where(_asset_tags.c.tag_id == keep_id)
        ).fetchall()
    }
    for row in bind.execute(
        sa.select(_asset_tags.c.id, _asset_tags.c.asset_id).where(
            _asset_tags.c.tag_id == drop_id
        )
    ).fetchall():
        if row.asset_id in existing:
            bind.execute(sa.delete(_asset_tags).where(_asset_tags.c.id == row.id))
            continue
        bind.execute(
            sa.update(_asset_tags)
            .where(_asset_tags.c.id == row.id)
            .values(tag_id=keep_id)
        )


def _move_slot_refs(bind: sa.Connection, *, drop_id: str, keep_id: str) -> None:
    """把模板槽位里指向 `drop_id` 的引用改成 `keep_id`。

    `tag_id` 是老形状（单个标签），`tag_ids` 是新形状（JSON 列表）——
    两列都要改，只改一列会让「读时退回老列」的路径拿到已删的 id。
    """
    bind.execute(
        sa.update(_slots).where(_slots.c.tag_id == drop_id).values(tag_id=keep_id)
    )
    for row in bind.execute(
        sa.select(_slots.c.id, _slots.c.tag_ids).where(_slots.c.tag_ids.is_not(None))
    ).fetchall():
        values = row.tag_ids if isinstance(row.tag_ids, list) else []
        if drop_id not in values:
            continue
        replaced: list[object] = []
        for item in values:
            if item == drop_id:
                if keep_id not in replaced:
                    replaced.append(keep_id)
            else:
                replaced.append(item)
        bind.execute(
            sa.update(_slots).where(_slots.c.id == row.id).values(tag_ids=replaced)
        )


def _new_id() -> str:
    return uuid.uuid4().hex
