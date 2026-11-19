"""重建封装成员表，槽位加 pick_mode

Revision ID: 3ebfdacfbcca
Revises: 667e0540179b
Create Date: 2026-11-19 20:20:00.000000

## 为什么把封装请回来

`c3d8e1f04a7b`（2026-11-16）下线过封装，理由是「它让一条素材不再是唯一的事实」。
现在重新引入，目标很具体：模板里「正面图 / 三视图 / 细节图」是同一件产品的三个
面，使用者在模板库要选三次；有了包，选一次就填满。

**这次比上次多的约束**（对应当时下线的那几个分支）：
- 成员**不再单列**（`library_pack_items` 有行 = 这条素材只通过包出现）。
  列表过滤落在 SQL 里（与 `exclude_kinds` 同一个理由：`total` 与 `items` 同口径）。
- 包本身是 `kind='pack'` 的资产行（无自己的字节），所以下载/预览仍走既有路径，
  不为它单开分支 —— 包没有 `storage_key`，取文件会直接 404 而不是静默给空。

## 可逆

只加两张结构（一张新表、一根带默认值的列），不删任何数据：
- 降：删 `library_pack_items`、删 `template_spec_slots.pick_mode`（都是本次新增的）。

「删表会丢掉包与成员的归属」这件事不构成不可逆：那些行是按新功能建出来的，
回到旧代码它们本来也没有任何读者。所以**不标** `irreversible`。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "3ebfdacfbcca"
down_revision: Union[str, None] = "667e0540179b"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "library_pack_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("pack_id", sa.String(32), nullable=False),
        sa.Column("asset_id", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_library_pack_items", "library_pack_items", ["pack_id", "asset_id"], unique=True
    )
    op.create_index("ix_library_pack_items_asset", "library_pack_items", ["asset_id"])
    op.create_index(
        "ix_library_pack_items_pack", "library_pack_items", ["pack_id", "position"]
    )
    op.create_index(
        "ix_library_pack_items_created_at", "library_pack_items", ["created_at"]
    )

    op.add_column(
        "template_spec_slots",
        sa.Column(
            "pick_mode",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'asset'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("template_spec_slots", "pick_mode")
    op.drop_index("ix_library_pack_items_created_at", table_name="library_pack_items")
    op.drop_index("ix_library_pack_items_pack", table_name="library_pack_items")
    op.drop_index("ix_library_pack_items_asset", table_name="library_pack_items")
    op.drop_index("uq_library_pack_items", table_name="library_pack_items")
    op.drop_table("library_pack_items")
