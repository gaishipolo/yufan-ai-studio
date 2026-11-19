"""随机档改成「整段提示词」：版本表加 random_prompt，槽位表删 random_texts

Revision ID: 667e0540179b
Revises: 77ceaa5a36ac
Create Date: 2026-11-19 18:30:00.000000

## 为什么换一种做法

上一版（`77ceaa5a36ac`）把「随机」拆进每个可选槽位的多条文案里，随机档由前端
抽下标、后端替换 `empty_text`。实际用下来，要变的往往是**整个场景怎么写**，
而不是某一句兜底文案 —— 作者得在十几个格子里各写几条，写完还是拼不出一个
完整的新场景。

改成：作者在提示词下面另写**一整段**随机档提示词，随机档时整段替换主提示词。
随机性因此落在「用哪一段」上，而不是「每一句取第几条」。

## 可逆

只动结构（加一列、删一列），不涉及表或能力的下线：
- 升：加 `template_spec_versions.random_prompt`（空串默认），删槽位的
  `random_texts`。
- 降：加回 `template_spec_slots.random_texts`（JSON，可空），删
  `random_prompt`。

被删的 `random_texts` 里的文案不会回来 —— 那是被取代的旧机制的输入，
回到旧代码也只是一堆没人读的空数组。所以**不标** `irreversible`：
`downgrade()` 能正常走完，CI 的降级往返不受影响。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "667e0540179b"
down_revision: Union[str, None] = "77ceaa5a36ac"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "template_spec_versions",
        sa.Column(
            "random_prompt",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.drop_column("template_spec_slots", "random_texts")


def downgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column("random_texts", sa.JSON(), nullable=True),
    )
    op.drop_column("template_spec_versions", "random_prompt")
