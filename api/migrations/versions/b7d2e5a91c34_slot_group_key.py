"""槽位加「封装容器」分组（group_key）

## 为什么

用户的口径是：**封装也作为一种槽位，可以在它内部放别的槽位**。几个槽位在同一个
容器里，就只出一个选包入口 —— 点一次包，按标签把成员分到各槽位。上一版是
「每一格各自列一遍包」，同一个包在两个槽位的网格里各出现一次，点哪个都填两格，
看起来像两个独立的选择（用户报的就是这个）。

容器不是一条独立记录：它就是槽位上的一列 `group_key`。同一 key 的槽位在顺序上
必须相邻（`rules._validate_groups` 会拦），界面据此画成一个框。

## 可逆

纯加列（默认空串 = 不属于任何容器），`downgrade()` 删列即可，**不标** `irreversible`。
存量模板全部是独立槽位，行为不变。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e5a91c34"
down_revision: Union[str, None] = "9c1e4f7a2b58"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column(
            "group_key",
            sa.String(length=40),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("template_spec_slots", "group_key")
