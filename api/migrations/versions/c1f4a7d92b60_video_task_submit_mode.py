"""video_tasks: 补供应商形态快照

Revision ID: c1f4a7d92b60
Revises: a9d3c7e15b42
Create Date: 2026-11-28 11:00:00.000000

视频链改用 framework recovery 后，判断「有 external_id 的孤儿能不能续轮询」
必须与 image / model_driver 同口径：提交时冻结供应商形态，而不是恢复时按
当前模型目录重新解释。老记录留 NULL，恢复器按安全侧（不能续查）处理。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1f4a7d92b60"
down_revision: Union[str, Sequence[str], None] = "a9d3c7e15b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "video_tasks",
        sa.Column("submit_mode", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_tasks", "submit_mode")