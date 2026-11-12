"""library_asset_generation.prompt 放宽到 TEXT.

`prompt` 存的是**注入正文之后**的提示词：`{{Text N}}` 在提交期就被换成整段文案，
单份文本资产上限 1MB，所以这列从诞生起就没有 4000 字这个前提。`String(4000)`
在 PG 上会抛 `value too long for type character varying(4000)`，而这条插入发生在
`runner._archive` 里、被 `except Exception` 兜成一行日志 —— 表现是「任务显示成功、
草稿箱里没有这张图」，且没有任何重试路径。

SQLite 不校验 VARCHAR 长度，本地与 CI 全绿，只有 PG 环境能复现，所以这里既放宽
列、也补一条按 §数据断言 的用例（见 `tests/test_library_text_refs.py`）。

downgrade 会截断超长行：`String(4000)` 装不下注入过正文的提示词，不截断就降不回去。
用 `substr` 是因为它在 PG 与 SQLite 上同义。
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c4e2a18d63"
down_revision = "e2b7c4a9f310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("library_asset_generation", schema=None) as batch_op:
        batch_op.alter_column(
            "prompt",
            existing_type=sa.String(length=4000),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE library_asset_generation SET prompt = substr(prompt, 1, 4000) "
        "WHERE length(prompt) > 4000"
    )
    with op.batch_alter_table("library_asset_generation", schema=None) as batch_op:
        batch_op.alter_column(
            "prompt",
            existing_type=sa.Text(),
            type_=sa.String(length=4000),
            existing_nullable=False,
        )
