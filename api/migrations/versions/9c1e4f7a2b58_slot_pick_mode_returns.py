"""把「槽位素材从哪来」加回槽位表（pick_mode）

## 为什么又加回来

2026-11-19 删它，是因为当时的做法是「包和素材并排在同一个网格里，靠标签
自动归位」—— 槽位不需要声明来源。用户的真实心智模型不是这样：**槽位只有两种
情况，一是来自素材，一是来自包**，而且几个槽位可以都来自同一个包（选一次包，
一起填好）。

差别不在界面，在**判据**：靠标签自动归位时，任何一个标签撞上的槽位都能被包填，
作者没法声明「这几格是一整套，必须一起换」。加回来源轴之后，只有声明为
`pack` 的槽位参与分包。仍然一格一张 —— 包解决的是「一次挑好几格」。

## 与 2026-11-14 删掉的 `source` 不是同一根轴

那根 `source` 与 `type` 抢同一件事（槽位类型里还有个 `pack`），能收到
`type=image, source=pack` 这种自相矛盾的写法。这里的 `pick_mode` 只回答
「从哪儿挑」，`type` 仍然只回答「要什么」。

## 可逆

纯加列（有默认值），`downgrade()` 把列删掉即可，**不标** `irreversible`。
存量行按 `asset` 解释（老模板的槽位仍旧从素材里挑）。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c1e4f7a2b58"
down_revision: Union[str, None] = "67b2a1d5dfb3"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column(
            "pick_mode",
            sa.String(length=8),
            nullable=False,
            server_default="asset",
        ),
    )


def downgrade() -> None:
    op.drop_column("template_spec_slots", "pick_mode")
