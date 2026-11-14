"""提示词合并成一段：`prompt_locked` + `prompt_editable` → `prompt`

Revision ID: c9a1f4e27b53
Revises: b8e4c1f90d27
Create Date: 2026-11-14 16:20:00.000000

## 为什么合并

2026-11-14 用户拍板：「提示词不用区分固定段和可编辑段，如果某些地方要可变，
通过文本插槽实现」。原来那两段的存在理由是「模板库可以改 editable 段」，而
`compile` 的 `promptOverride` 就是为了这件事；现在可变内容改由 `text` 槽位承载
（填表时选一份文案 → 编译成 `{{Text N}}` → 执行层按 `textLibraryIds` 注入正文），
模板库不再改任何文字。两段一合并，`compile` 的入参也就不用再区分谁能改。

## 数据怎么迁

- `template_spec_versions.prompt_locked` / `prompt_editable` → `prompt`：
  非空的两段用**两个换行**拼起来（与原 `_join` 的分隔一致），空段不留空行；
- `template_specs.draft` 里的 `{"prompt": {"locked":…, "editable":…}}` →
  `{"prompt": "…"}`：草稿是整份 JSON，逐行读出来改再写回（两种方言同一套代码）。

## 降级

结构能回去，**语义回不去**：`prompt` 整段写进 `prompt_locked`、`prompt_editable`
留空。文字一个字都不丢，但"哪一段是模板库可改的"这个区分已经不存在了。
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9a1f4e27b53"
down_revision: Union[str, Sequence[str], None] = "b8e4c1f90d27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> tuple[sa.Table, sa.Table]:
    meta = sa.MetaData()
    versions = sa.Table(
        "template_spec_versions",
        meta,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("prompt", sa.Text),
        sa.Column("prompt_locked", sa.Text),
        sa.Column("prompt_editable", sa.Text),
    )
    heads = sa.Table(
        "template_specs",
        meta,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("draft", sa.JSON),
    )
    return versions, heads


def _merge(locked: str | None, editable: str | None) -> str:
    """两段之间固定两个换行；空段直接略过（与旧 `_join` 的口径一致）。"""
    return "\n\n".join(part for part in (locked or "", editable or "") if part)


def upgrade() -> None:
    with op.batch_alter_table("template_spec_versions") as batch_op:
        batch_op.add_column(sa.Column("prompt", sa.Text(), nullable=False, server_default=""))

    versions, heads = _tables()
    bind = op.get_bind()

    rows = bind.execute(
        sa.select(versions.c.id, versions.c.prompt_locked, versions.c.prompt_editable)
    ).all()
    for row in rows:
        bind.execute(
            versions.update()
            .where(versions.c.id == row.id)
            .values(prompt=_merge(row.prompt_locked, row.prompt_editable))
        )

    for row in bind.execute(sa.select(heads.c.id, heads.c.draft)).all():
        draft: Any = row.draft
        if not isinstance(draft, dict):
            continue
        prompt = draft.get("prompt")
        if not isinstance(prompt, dict):
            continue
        draft = {**draft, "prompt": _merge(prompt.get("locked"), prompt.get("editable"))}
        bind.execute(heads.update().where(heads.c.id == row.id).values(draft=draft))

    with op.batch_alter_table("template_spec_versions") as batch_op:
        batch_op.drop_column("prompt_editable")
        batch_op.drop_column("prompt_locked")
        # 回填完就把默认值撤掉：模型侧没有 server_default，留着会被
        # `compare_metadata` 判成"迁移多给了一列默认值"。
        batch_op.alter_column("prompt", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("template_spec_versions") as batch_op:
        batch_op.add_column(
            sa.Column("prompt_locked", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("prompt_editable", sa.Text(), nullable=False, server_default="")
        )

    versions, heads = _tables()
    bind = op.get_bind()

    # 结构能回去、语义回不去：整段写进 locked，可编辑段留空。
    for row in bind.execute(sa.select(versions.c.id, versions.c.prompt)).all():
        bind.execute(
            versions.update()
            .where(versions.c.id == row.id)
            .values(prompt_locked=row.prompt or "")
        )

    for row in bind.execute(sa.select(heads.c.id, heads.c.draft)).all():
        draft: Any = row.draft
        if not isinstance(draft, dict) or not isinstance(draft.get("prompt"), str):
            continue
        draft = {
            **draft,
            "prompt": {"locked": draft["prompt"], "editable": ""},
        }
        bind.execute(heads.update().where(heads.c.id == row.id).values(draft=draft))

    with op.batch_alter_table("template_spec_versions") as batch_op:
        batch_op.drop_column("prompt")
