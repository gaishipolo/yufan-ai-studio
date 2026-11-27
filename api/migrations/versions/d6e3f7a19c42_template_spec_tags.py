"""模板独立两级标签词表 + 模板 head 上的 tag_ids（2026-11-27）。

模板库需要按标签筛选、模板封装的列表页要给模板打标签。词表**独立于素材库**
（两套分类维度互不影响），模板标签存在 head 的 JSON 列里 —— 改标签立即生效，
不进发布快照，历史任务的版本不被改动。

可逆：downgrade 删列、删表。降级会丢标签数据（跟绝大多数加字段迁移不同），
因为整张词表都是这次新建的；模板上的 tag_ids 也一并消失，但 draft/版本快照
不含标签，所以不会让历史模板读不出来。
"""

import sqlalchemy as sa
from alembic import op

revision = "d6e3f7a19c42"
down_revision = "b7e2c4a19f30"
branch_labels = None
depends_on = None

# 不标 irreversible：downgrade 真的能 DROP 回来（会丢标签数据，但不抛异常）。
# 标记的语义是「downgrade() 会抛」，只删数据不改结构不标 —— 否则锚点被推到
# 链头，降级往返只剩一两道迁移可走，等于没测。见 tests/test_downgrade_anchor.py。


def upgrade() -> None:
    op.create_table(
        "template_tags",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("parent_id", sa.String(length=32), server_default="", nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "parent_id", name="uq_template_tags_name"),
    )
    op.create_index("ix_template_tags_parent", "template_tags", ["parent_id"], unique=False)

    with op.batch_alter_table("template_specs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("tag_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("template_specs", schema=None) as batch_op:
        batch_op.drop_column("tag_ids")
    op.drop_index("ix_template_tags_parent", table_name="template_tags")
    op.drop_table("template_tags")
