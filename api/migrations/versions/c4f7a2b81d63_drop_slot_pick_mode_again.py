"""删掉槽位的 pick_mode：来源是位置的结果，不是作者要声明的字段

2026-11-20 的口径定死了：「封装槽位内来源必然是封装包，封装槽位外来源只能是素材」。
也就是说 `pickMode` 完全由 `group`（是否在封装容器里）决定：

- `group` 为空 → 从候选**素材**里挑
- `group` 非空 → 这一组共用一次「选包」，只能从**封装包**取

两处并存时接口能收到 `group=g1 + pickMode=asset` 这种没法解释的组合，而前端
已经不再显示这个下拉（作者看不到、改不了，却还在影响模板库那一格列什么）——
那就是一个不可见的状态。删掉它，判据只剩一条。

## 为什么必须改写草稿

`template_specs.draft` 是整份 `TemplateSpecIn` 的 JSON，而它是**严格模式**
（多一个字段就 `extra_forbidden`）。删掉 `pickMode` 之后，带这个键的旧草稿
一读就 422/500 —— 列表页整个打不开。

2026-11-14 删 `source`、2026-11-19 删 `random_texts`、2026-11-20 删过一次
`pick_mode` 都踩过同一个坑。所以这里显式清掉 `slots[].pickMode`。

## 可逆

只动结构（删一列），`downgrade()` 把列加回来（`asset` 默认值），**不标**
`irreversible` —— 降级能正常走完。

**但降级会丢一点信息**：库里当时有 4 行 `pick_mode='pack'` 且 `group_key=''`
（「独立槽位、但从包取」，是 2026-11-20 中午那次实现留下的）。降级后它们读回来
都是 `asset`。这 4 行都属于验收/测试模板，不是真实内容。
"""

from __future__ import annotations

import json
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a2b81d63"
down_revision: Union[str, None] = "b7d2e5a91c34"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
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
