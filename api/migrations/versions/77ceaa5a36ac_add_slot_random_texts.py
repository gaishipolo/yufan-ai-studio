"""给可选槽位加「随机文案」字段

Revision ID: 77ceaa5a36ac
Revises: 4e06879f9f75
Create Date: 2026-11-19 16:10:00.000000

## 为什么加

模板封装的可选槽位此前只有两段文案：选中时 / 未选时。新增「随机文案」
（`random_texts`，list[str]）供模板库的「随机」档使用：使用者选随机档时
不传素材，由前端从这批文案里抽一条注入提示词 —— 同一模板每次生成的
场景描述不同，视频不至于千篇一律。

与 `tag_ids` 一样用 JSON 列存 list；nullable=True 让历史行读到 None，
service 读侧按空列表处理（见 `spec_service._slot_out`）。

## 降级

删列即可；随机文案是新增数据，降级后只是不再可选，不破坏别的字段。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "77ceaa5a36ac"
down_revision: Union[str, None] = "4e06879f9f75"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column("random_texts", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("template_spec_slots", "random_texts")
