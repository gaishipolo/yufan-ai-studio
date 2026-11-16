"""槽位的候选集标签从「一个」放宽成「一组」：`template_spec_slots.tag_ids`

Revision ID: d9e4f1a83c07
Revises: f3a8c1d5e902
Create Date: 2026-11-17 00:40:00.000000

## 为什么改

槽位原来只有一个 `tag_id`：候选集只能是**一个**一级标签，于是「产品 + 角色」
这种真实需求没法表达 —— 作者只能挑一个分组，模板使用者看到的候选集也就被限死
在一个分支里。

现在槽位可以圈**一组**标签（任意级别：分组 / 系列 / 具体词），多个标签之间是
并集。资产库那边的筛选口径本来就是 OR + 「展开成自身与全部下级」，所以
列表、计数与选择器三处不用改 —— 变的只是"能圈几个"。

## 迁移什么

1. 加列 `tag_ids`（JSON，可空）：新的候选集。
2. **不做回填**：老行只剩 `tag_id` 一个值，读的时候由服务层回退（`tag_ids`
   为空就退回旧的 `tag_id`）。回填要动数据、还要处理 JSON 列的方言差异，而
   回退读一次就够 —— 老模板照样能用，只是发布新版本后才会写成新形状。
3. `tag_id` 列**保留**：它既是老行的唯一真相，也是降级时旧代码看得懂的落点
   （新写入的行两列都填，`tag_id` 记第一个标签）。

## 降级

删掉 `tag_ids` 列即可。槽位的候选集退回「只能圈一个标签」，多标签的那部分
信息会丢 —— 但那份信息在草稿 JSON（`template_specs.draft`）里还在，重新保存
一次草稿就能补回来。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e4f1a83c07"
down_revision: str | None = "f3a8c1d5e902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column("tag_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("template_spec_slots") as batch:
        batch.drop_column("tag_ids")
