"""image: 对账凭证改为追加语义

Revision ID: a9d3c7e5b210
Revises: f4c2d8e91b70
Create Date: 2026-11-06 14:35:00.000000

`previous_external_id` 只有一个槽位：一条记录连续两次「确认已扣费」重试后，
第一份账单的任务号被第二次覆盖，而这正是 R12-01 想保住的凭证（R13-02）。
审计 detail 按设计吞异常，不能当唯一备份——两份会在同一轮里一起丢。

新增 `external_id_history` 存 JSON 数组（旧的在前），`previous_external_id`
保留为「最近一个」，出参 `previousExternalId` 语义不变，另加
`previousExternalIds` 给全量历史。

存文本而不是 JSONB：sqlite 与 PG 共用一套代码，且这张表从不需要按历史内容
查询或建索引。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9d3c7e5b210"
down_revision: Union[str, Sequence[str], None] = "f4c2d8e91b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column("external_id_history", sa.Text(), nullable=True),
    )
    # 把已有的单槽凭证搬进历史，否则升级后第一次重试会把老凭证覆盖掉
    op.execute(
        sa.text(
            "UPDATE image_jobs "
            "SET external_id_history = '[\"' || previous_external_id || '\"]' "
            "WHERE previous_external_id IS NOT NULL AND external_id_history IS NULL"
        )
    )


def downgrade() -> None:
    # 降级只保留最近一个凭证：历史数组塞不回单列，多轮重试的那几份会丢，
    # 这是降级本身的代价，写在这里免得有人以为「降级无损」。
    op.drop_column("image_jobs", "external_id_history")
