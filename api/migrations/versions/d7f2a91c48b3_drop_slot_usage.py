"""删掉槽位的「进提示词的用途说明」列：`template_spec_slots.usage_text`

Revision ID: d7f2a91c48b3
Revises: b1f4c8d2e506
Create Date: 2026-11-18 15:20:00.000000

## 为什么删

`usage` 从来**没有进过提示词**。编译器（`core/template_spec/compile.py`）
完全不看它 —— 它既不参与 `{{slot:key}}` 的展开，也不进 `CompiledRequest` 的
任何字段。它只在前端显示一行字。

于是它做的事比不做还糟：

- 作者填了「确定同款同色外观」，以为这句话管用；
- 模板库的使用者看到这一行，以为提示词里真的有这句约束；
- 而提示词里根本没有 —— 模型看不到它。

**说了等于没说，还让人以为说了。** 这比没有这个字段更坏：没有的时候
没人会指望它，有了的时候它就在骗人。

## 为什么数据无损

迁移前盘点过：113 条已发布槽位里，`usage_text` **全部是空串**。
一个从未被写入过的列，删掉它没有任何信息损失。

## 为什么这条迁移是**可逆**的（没有 `irreversible = True`）

降级重建一个 `usage_text` 列就能装回原样 —— 因为原内容**本来就是空的**。
重建出来的空列与删掉之前的空列完全等价。

链上那两道带 `irreversible = True` 的迁移（`b8e4c1f90d27` 下线替换链、
`c3d8e1f04a7b` 下线素材封装）都是**删表**：数据丢了就是丢了，
重建空表装不回任何东西。这条不一样 —— 删的是一列**空数据**。

所以这条**不标**不可逆：标了会让 CI 的降级往返测试在撞上它时抛
`NotImplementedError`，而它明明可以安全往返。降级往返能真正走通
「加两列 → 删一列 → 加回来」这条路径，本身就是这条迁移写对了的证据。

## 契约层怎么办

`SpecSlotIn` 是 `extra="forbid"`：老草稿里带着 `usage` 会被判成「未知键」，
那份草稿**一打开就 422** —— 模板还在，只是没人能再编辑它。

所以契约层加了一个 `_drop_legacy_usage` 前置校验，把老键丢掉
（与老 `tagId` → `tagIds`、`packId` 丢弃是同一套处理）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f2a91c48b3"
down_revision: str | Sequence[str] | None = "b1f4c8d2e506"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("template_spec_slots", "usage_text")


def downgrade() -> None:
    """重建一个空列：原内容本来就是空的，重建出来与删之前等价。

    **可逆**：这列从来没被写入过（迁移前盘点：113 条槽位全是空串），
    所以「删掉」与「重建一个空列」在数据上完全等价 —— 这条迁移因此
    不标 `irreversible = True`，CI 的降级往返能真正走通它。

    宽度与原列一致（`String(500)`）：重建出来的形状要与原来一样，
    否则将来真有人往里写东西时会撞到长度限制。
    """
    op.add_column(
        "template_spec_slots",
        sa.Column("usage_text", sa.String(length=500), nullable=False, server_default=""),
    )
