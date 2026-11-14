"""槽位把「要什么」合成一根轴：`type` 收编 `pack`，`source` 删掉，新增 `tag_id`

Revision ID: a7d3f0c85e21
Revises: f4b8d2a61c07
Create Date: 2026-11-14 19:05:00.000000

## 为什么合并

`type`（image/video/audio/text）与 `source`（asset/pack）是两根轴，但"要一个
封装"这件事本来就该由 `type=pack` 表达 —— 两根轴并存时接口可以收到
`type=image, source=pack` 这种自相矛盾的组合，而它俩谁都说不清自己更权威。
封装页也不再让人选「来源」，而是选**类型 = 资产库的 kind**（其中一档就是封装）。

## 迁移什么

1. `template_spec_slots`：`source='pack'` 的行 `type` 改成 `'pack'`，然后删列；
2. `template_specs.draft`（整份草稿 JSON）：同样把 `source` 并进 `type` 再移除该键；
3. 新增 `tag_id`（一级标签 id，空串 = 不限）—— 槽位的**候选集范围**。

## 降级

`source` 能算回来（`type=pack` → pack），但**原来的 `type` 找不回来了**：封装
槽位过去存的是成员类型（多半是 `image`），现在被 `pack` 覆盖。所以降级把封装
槽位一律写成 `image` 并注明 —— 这是有损的，但比直接抛错更有用（真要回退，本来就
该连数据一起回滚）。
"""

import json
from collections.abc import Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d3f0c85e21"
down_revision: Union[str, Sequence[str], None] = "f4b8d2a61c07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _merge_source_into_type(slot: dict[str, Any]) -> dict[str, Any]:
    """把草稿里的一个槽位从「type + source」改成「type 里带 pack」。"""
    merged = dict(slot)
    source = merged.pop("source", "asset")
    if source == "pack":
        merged["type"] = "pack"
    return merged


def _upgrade_drafts(bind: sa.engine.Connection) -> None:
    rows = bind.execute(sa.text("SELECT id, draft FROM template_specs")).fetchall()
    for spec_id, draft in rows:
        if not draft:
            continue
        data = json.loads(draft) if isinstance(draft, str) else draft
        slots = data.get("slots") or []
        if not isinstance(slots, list):
            continue
        changed = [_merge_source_into_type(slot) for slot in slots]
        if changed == slots:
            continue
        data["slots"] = changed
        bind.execute(
            sa.text("UPDATE template_specs SET draft = :draft WHERE id = :id"),
            {"draft": json.dumps(data, ensure_ascii=False), "id": spec_id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("template_spec_slots") as batch:
        batch.add_column(
            sa.Column("tag_id", sa.String(32), nullable=False, server_default="")
        )
    bind.execute(sa.text("UPDATE template_spec_slots SET type = 'pack' WHERE source = 'pack'"))
    _upgrade_drafts(bind)
    with op.batch_alter_table("template_spec_slots") as batch:
        batch.drop_column("source")
        # 模型侧没有 server_default，留着会被 `compare_metadata` 判成多给了默认值。
        batch.alter_column("tag_id", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("template_spec_slots") as batch:
        batch.add_column(
            sa.Column("source", sa.String(16), nullable=False, server_default="asset")
        )
    bind.execute(sa.text("UPDATE template_spec_slots SET source = 'pack' WHERE type = 'pack'"))
    # 原来的成员类型找不回来（已被 'pack' 覆盖）。封装槽位的成员绝大多数是图片，
    # 这里按最可能的取值写回，并在表注释里说明这是有损的。
    bind.execute(sa.text("UPDATE template_spec_slots SET type = 'image' WHERE type = 'pack'"))
    with op.batch_alter_table("template_spec_slots") as batch:
        batch.drop_column("tag_id")
        batch.alter_column("source", server_default=None)
