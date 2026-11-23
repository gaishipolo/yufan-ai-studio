"""去字幕派生任务：派生关系、变体标记、资产指针。

「去字幕版」不是新表，而是 `video_tasks` 里的一条**派生任务**（生命周期与普通任务同构，
复用 runner 的轮询/重试/下载/入库）。三列：

- `derived_from_task_id`：派生自哪条任务；用户主动创建的任务为 NULL。
- `variant`：`original` / `subtitle_erased`。老数据默认 `original`，不需要回填。
- `library_asset_id`：这条任务对应的资产库资产 id。派生任务要用它把「原片资产」
  原地替换成去字幕版（资产库只保留一版），否则资产库里会同时躺着两段几乎一样的视频。

唯一索引是「点两次不会下两单」的硬保证：应用层 `if exists` 在并发下挡不住。
"""

from alembic import op
import sqlalchemy as sa

revision = "d2a6f4c81b90"
down_revision = "b7c4e91f2a58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_tasks", sa.Column("derived_from_task_id", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "video_tasks",
        sa.Column("variant", sa.String(length=24), nullable=False, server_default="original"),
    )
    op.add_column(
        "video_tasks", sa.Column("library_asset_id", sa.String(length=32), nullable=True)
    )
    op.create_index(
        "ix_video_tasks_derived_from_task_id", "video_tasks", ["derived_from_task_id"], unique=False
    )
    op.create_index(
        "ux_video_tasks_derived_variant",
        "video_tasks",
        ["derived_from_task_id", "variant"],
        unique=True,
        postgresql_where=sa.text("derived_from_task_id IS NOT NULL"),
        sqlite_where=sa.text("derived_from_task_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_video_tasks_derived_variant", table_name="video_tasks")
    op.drop_index("ix_video_tasks_derived_from_task_id", table_name="video_tasks")
    op.drop_column("video_tasks", "library_asset_id")
    op.drop_column("video_tasks", "variant")
    op.drop_column("video_tasks", "derived_from_task_id")
