"""封装容器有了名字：`template_spec_slots.group_label`

作者在封装页给容器起名（默认「封装槽位 1」），模板库那一块就按这个名字显示，
不再是写死的「封装槽位」。

## 名字存组内每一行，不新增容器表

发布快照只有槽位行（`template_spec_slots`），容器是「`group_key` 相同的相邻
一串」。要给它配一个名字有两条路：

- 新建 `template_spec_groups` 表：规范，但每次读快照要多一条查询（或一层
  relationship），而且「有容器行、没有成员」这种空壳要额外清理；
- 组内每行冗余一列：读快照时顺手就有，顺序天然按 `position`。

选后者。冗余只在这一处、只在发布快照里（草稿的 `groups[]` 是唯一来源），
且读侧取**首个非空**值 —— 就算有人手改过其中一行，行为也是确定的。

## 不回填、可逆

老版本的容器没有名字。**不回填**：迁移里写不出「第几个容器」这种业务含义
（顺序在发布时才算得出），而且回填了也会和前端回退出来的默认名一致，
白白改一遍数据。读侧空串 → 前端按段序显示「封装槽位 N」。

`downgrade()` 只删列，不抛，所以**不标** `irreversible`。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "8d2a4f61c9e3"
down_revision: Union[str, None] = "b3e1f7a29c04"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_TABLE = "template_spec_slots"


def upgrade() -> None:
    # batch：sqlite 没有 ALTER TABLE，只能重建整张表（本仓迁移的既有约定）
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "group_label",
                sa.String(length=80),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_column("group_label")
