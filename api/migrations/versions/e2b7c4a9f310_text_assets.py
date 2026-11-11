"""Text assets (kind=text) + prompt text references.

一次迁移覆盖两件事，因为它们共用同一套「正文快照」语义，拆成两条只会让
中间态的部署出现「表有了、列没有」：

1. 资产库：`library_assets` 加 `text_excerpt` / `text_version`，新建
   `library_text_versions`（不可变版本快照，正文行内留存）。
2. 生成侧：`image_jobs` / `video_tasks` / `replacement_runs` 加 `text_json`，
   存 `{{Text N}}` 注入用的**冻结快照**（id / name / version / sha256 / content）。
   不落库 = 重试时重新读资产，用户改完稿后历史任务会静默换一段不同的提示词。

存量行没有文本资产（`kind` 是新增枚举值），所以不需要回填正文；四列的
`server_default` 让老记录在读取时天然是「没有文本引用」。

`library_text_versions` 刻意不带外键：与项目其它表一致，purge 时由
`lifecycle._purge_row` 显式删除，级联会在并存期把别的表一起带走。
"""

import sqlalchemy as sa
from alembic import op

revision = "e2b7c4a9f310"
down_revision = "c3d7e9f1a420"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "library_assets",
        sa.Column("text_excerpt", sa.String(length=320), nullable=False, server_default=""),
    )
    op.add_column(
        "library_assets",
        sa.Column("text_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "library_text_versions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # 模型里是 `Index(..., unique=True)` 而不是 `UniqueConstraint`：
    # 两者在 PG 上都唯一，但 autogenerate 会把它识别成不同的对象，
    # `alembic check` 就报「模型有索引、库里没有」。
    op.create_index(
        "uq_library_text_versions_number",
        "library_text_versions",
        ["asset_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_library_text_versions_asset",
        "library_text_versions",
        ["asset_id", "created_at"],
    )

    for table in ("image_jobs", "video_tasks", "replacement_runs"):
        op.add_column(
            table,
            sa.Column("text_json", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    for table in ("replacement_runs", "video_tasks", "image_jobs"):
        op.drop_column(table, "text_json")
    op.drop_index("ix_library_text_versions_asset", table_name="library_text_versions")
    op.drop_index("uq_library_text_versions_number", table_name="library_text_versions")
    op.drop_table("library_text_versions")
    op.drop_column("library_assets", "text_version")
    op.drop_column("library_assets", "text_excerpt")
