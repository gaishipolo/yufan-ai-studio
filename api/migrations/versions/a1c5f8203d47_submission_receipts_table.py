"""提交回执落库：新增 submission_receipts 表

Revision ID: a1c5f8203d47
Revises: b7c4e91f2a58
Create Date: 2026-11-23 19:20:00.000000

## 为什么新建这张表

提交回执原来在**运行工作区文件**里（`records/bailian-submission.json`），和工作区
同生共死。工作区误删 / 换机器 / 多实例时，恢复路径读不到回执，会把同一个
`request_id` **重新提交给上游** —— 二次计费（2026-11-23 拍板「直接切 DB」）。

`(provider, run_id)` 唯一约束替代原来的 `O_EXCL` 文件抢占：有事务、有备份、
多实例一致。

## 数据搬迁

存量文件回执的搬迁**不在这个迁移里做**：Alembic 迁移不应该读运行工作区
（工作区可能不在本机，多实例下更是如此）。搬迁由独立的 CLI 命令完成，
在冻结提交通道后执行、全量校验通过才解冻。详见
`modules/model_driver/receipts.py::migrate_legacy_receipts` 与设计文档 §4 阶段 0.5。

## 可逆性

downgrade 删表即可（文件回执只读保留 30 天，是回滚安全网）—— **不标
`irreversible`**。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c5f8203d47"
down_revision = "b7c4e91f2a58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_receipts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column("channel_used", sa.String(length=32), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "run_id", name="uq_submission_receipts_provider_run"),
    )
    op.create_index(
        "ix_submission_receipts_created_at",
        "submission_receipts",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_submission_receipts_provider",
        "submission_receipts",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_submission_receipts_run_id",
        "submission_receipts",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_submission_receipts_status",
        "submission_receipts",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_submission_receipts_status", table_name="submission_receipts")
    op.drop_index("ix_submission_receipts_run_id", table_name="submission_receipts")
    op.drop_index("ix_submission_receipts_provider", table_name="submission_receipts")
    op.drop_index("ix_submission_receipts_created_at", table_name="submission_receipts")
    op.drop_table("submission_receipts")
