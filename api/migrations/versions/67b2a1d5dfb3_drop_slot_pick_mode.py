"""删掉槽位的 pick_mode

「槽位声明只从封装包选」这个模式没有落地：模板库最后做成了**包和素材并排
在同一个网格里**（点包按标签一次填满多格），所以不需要「这一格只列包」的开关。
字段留着只会让后来人以为有这个能力，前端从头到尾没引用过它。

## 为什么必须改写草稿

`template_specs.draft` 是整份 `TemplateSpecIn` 的 JSON，而它是**严格模式**
（多一个字段就 `extra_forbidden`）。删掉 `pickMode` 之后，带这个键的旧草稿
一读就 422/500 —— 列表页整个打不开。

2026-11-14 删 `source`、2026-11-19 删 `random_texts` 都踩过同一个坑。
所以这里显式清掉 `slots[].pickMode`，而不是指望读侧容错：放松 strict 等于
把所有字段的校验一起放松，代价远大于一次数据清洗。

## 可逆

只动结构（删一列）。降级把列加回来（`asset` 默认值），**不标** `irreversible` ——
`downgrade()` 能正常走完。被删的值本来就全是 `asset`（前端从未设置过它），
不回来也无损。
"""

from __future__ import annotations

import json
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

revision: str = "67b2a1d5dfb3"
down_revision: Union[str, None] = "3ebfdacfbcca"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # 先清 JSON 再删列：顺序其实无所谓，但「先收拾数据、再动结构」读起来
    # 更像一次迁移该有的样子。
    _strip_pick_mode_from_drafts()
    op.drop_column("template_spec_slots", "pick_mode")


def downgrade() -> None:
    op.add_column(
        "template_spec_slots",
        sa.Column(
            "pick_mode",
            sa.String(length=8),
            nullable=False,
            server_default="asset",
        ),
    )


def _strip_pick_mode_from_drafts() -> None:
    """把存量草稿里的 `slots[].pickMode` 删掉。

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
            if isinstance(slot, dict) and "pickMode" in slot:
                slot.pop("pickMode")
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE template_specs SET draft = CAST(:draft AS JSON) WHERE id = :id"
                ),
                {"draft": json.dumps(draft, ensure_ascii=False), "id": row.id},
            )
