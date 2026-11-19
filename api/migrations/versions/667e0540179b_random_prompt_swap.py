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
  `random_texts`，并改写存量草稿。
- 降：加回 `template_spec_slots.random_texts`（JSON，可空），删
  `random_prompt`。

被删的 `random_texts` 里的文案不会回来 —— 那是被取代的旧机制的输入，
回到旧代码也只是一堆没人读的空数组。所以**不标** `irreversible`：
`downgrade()` 能正常走完，CI 的降级往返不受影响。

## 为什么必须改写草稿

`template_specs.draft` 是整份 `TemplateSpecIn` 的 JSON，而 `TemplateSpecIn`
是**严格模式**（多一个字段就 `extra_forbidden`）。删掉 `random_texts` 之后，
带这个键的旧草稿**一读就 422/500** —— 列表页整个打不开。

2026-11-14 那次删 `source` 字段踩过同一个坑（漏了改草稿，一打开就 422）。
所以这里显式清掉 `slots[].randomTexts`，而不是指望读侧容错：
放松 strict 等于把所有字段的校验一起放松，代价远大于一次数据清洗。
"""

from __future__ import annotations

import json
from typing import Any, Union

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
    _strip_random_texts_from_drafts()


def downgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column("random_texts", sa.JSON(), nullable=True),
    )
    op.drop_column("template_spec_versions", "random_prompt")


def _strip_random_texts_from_drafts() -> None:
    """把存量草稿里的 `slots[].randomTexts` 删掉。

    逐行读改写而不是一条 JSON 表达式：`draft` 是 `json` 列（不是 `jsonb`），
    没有 `#-` 这类原地删键的算子；而且槽位数不定，路径没法预先拼好。
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, draft FROM template_specs WHERE draft IS NOT NULL")
    ).fetchall()
    for row in rows:
        draft: Any = row.draft
        if isinstance(draft, (str, bytes)):
            draft = json.loads(draft)
        if not isinstance(draft, dict):
            continue
        changed = False
        for slot in draft.get("slots") or []:
            if isinstance(slot, dict) and "randomTexts" in slot:
                slot.pop("randomTexts")
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE template_specs SET draft = CAST(:draft AS JSON) WHERE id = :id"
                ),
                {"draft": json.dumps(draft, ensure_ascii=False), "id": row.id},
            )
