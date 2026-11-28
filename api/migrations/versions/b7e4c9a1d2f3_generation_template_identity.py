"""generation: 记录模板来源身份

Revision ID: b7e4c9a1d2f3
Revises: c1f4a7d92b60
Create Date: 2026-11-28 15:00:00.000000

模板库提交时把 `TemplateSpec.spec_key` / 发布版本 / 名称冻结进任务或生图行：
- key 用于累计使用次数与「用户 × 模板」关系；
- version 留给后续版本级统计；
- name 是提交时的展示快照，模板改名/删除后统计页仍有可读标签。

三列都可空：直接生成不经过模板；历史记录也没有这些信息，不按名称猜补。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4c9a1d2f3"
down_revision: Union[str, Sequence[str], None] = "c1f4a7d92b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("video_tasks", "image_jobs"):
        op.add_column(table, sa.Column("template_key", sa.String(length=80), nullable=True))
        op.add_column(table, sa.Column("template_version", sa.Integer(), nullable=True))
        op.add_column(
            table, sa.Column("template_name", sa.String(length=160), nullable=True)
        )
    op.create_index(
        "ix_video_tasks_template_author",
        "video_tasks",
        ["template_key", "author_id"],
    )
    op.create_index(
        "ix_image_jobs_template_owner",
        "image_jobs",
        ["template_key", "owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_image_jobs_template_owner", table_name="image_jobs")
    op.drop_index("ix_video_tasks_template_author", table_name="video_tasks")
    for table in ("image_jobs", "video_tasks"):
        op.drop_column(table, "template_name")
        op.drop_column(table, "template_version")
        op.drop_column(table, "template_key")