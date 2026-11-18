"""槽位加「选中时文案」与「未选时文案」两列：`template_spec_slots`

Revision ID: b1f4c8d2e506
Revises: d4a9c1e73b28
Create Date: 2026-11-18 13:10:00.000000

## 为什么改

今天「槽位由用户选择」= **必填**：使用者在模板库必须选素材，不选就报错。
需求是「可选」：选了就以选的为准，不选就用**提示词填充**。

「用提示词填充」这件事要有形状，否则只有两个坏选择：

- 把兜底文案写在提示词正文里 → 作者要把同一段话维护两遍
  （正文一遍、槽位一遍），两处措辞一漂移就对不上；
- 用正则在正文里找「人物参考 {{slot:hero}}」这一段再整段替换 → 段落边界靠猜，
  匹配不到时的行为没人说得清（报错？静默？），而猜错的后果是提示词缺一块。

所以把「这一段怎么说」搬进槽位：提示词正文里 `{{slot:key}}` 是**唯一插入点**，
槽位自己声明「有素材时这么说 / 没素材时那么说」。替换对象从「一段文字」变成
「一个确定的 token」。

## 迁移什么

加两列，都可空（默认空串）：

| 列 | 含义 | 空 = |
|---|---|---|
| `filled_text` | 选中时文案（可含自引用 `{{slot:key}}`） | 直接插 `{{Mixed N}}`（旧行为） |
| `empty_text` | 未选时文案（纯提示词，不含引用） | 编译期报错（可选槽位被引用时） |

**不做回填**：老行两列都是空串，语义就是「没填素材时按旧行为报错」——
与改动前完全一致。老模板不需要迁移就能继续用。

## 降级

删掉两列即可。降级后「未选时用兜底文案」这个能力消失（回到必填语义），
但那份文案还在 `template_specs.draft` 的 JSON 里，重新保存一次草稿就能补回来。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f4c8d2e506"
down_revision: str | Sequence[str] | None = "d4a9c1e73b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column("filled_text", sa.String(length=2000), nullable=False, server_default=""),
    )
    op.add_column(
        "template_spec_slots",
        sa.Column("empty_text", sa.String(length=2000), nullable=False, server_default=""),
    )


def downgrade() -> None:
    with op.batch_alter_table("template_spec_slots") as batch:
        batch.drop_column("empty_text")
        batch.drop_column("filled_text")
