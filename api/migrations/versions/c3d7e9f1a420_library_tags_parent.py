"""Library tags: two-level hierarchy.

给标签词表加 `parent_id`，一级包含二级。三层要点：

1. **空串表示一级**，不用 NULL：PG / SQLite 的唯一索引里 NULL 互不相等，
   `UNIQUE(name, parent_id)` 拦不住两个同名一级标签（见 models.py 注释）。
   存量 8 个标签靠 `server_default=''` 自动成为一级，不用单独回填这一列。
2. **唯一约束从 `(name)` 换成 `(name, parent_id)`**：同名规则变成「同一父下唯一」，
   不同一级下可以有同名二级。索引名保持不变，`alembic check` 只看到列集合变了。
3. **降级会丢层级**：把 `parent_id` 去掉后所有标签重新变成一级；若此时存在
   「不同父下的同名二级」，重建 `UNIQUE(name)` 会失败。这是有意为之——与其
   静默改名字，不如让运维明确知道降级会破坏什么。

存量标签的父子关系不在这条迁移里写死：环境之间词表不同，按名字硬编码会让
一个新部署凭空多出几个标签。归层走 `app library init-tag-tree`（幂等、默认试运行）。
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d7e9f1a420"
down_revision = "b4e8c1d6f920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "library_tags",
        sa.Column(
            "parent_id",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )
    op.drop_index("uq_library_tags_name", table_name="library_tags")
    op.create_index(
        "uq_library_tags_name", "library_tags", ["name", "parent_id"], unique=True
    )
    op.create_index("ix_library_tags_parent", "library_tags", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_library_tags_parent", table_name="library_tags")
    op.drop_index("uq_library_tags_name", table_name="library_tags")
    op.create_index("uq_library_tags_name", "library_tags", ["name"], unique=True)
    op.drop_column("library_tags", "parent_id")
