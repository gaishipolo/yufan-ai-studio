"""template_spec_slots 加 empty_text_from_anchor（2026-11-27）。

可选槽位的「未选时文案」按设计是**正文里框选出来的那一段**：提交时那段正文
让位成插入点，文字挪进 empty_text。库里以前**没记**这一点，于是打开草稿时
无从分辨「正文里那处引用是不是那段默认描述」—— 只能猜第一个，猜错就把作者
原话顶掉了（库里两处长得一模一样，分不出来）。

这一列就是那个「是不是」。老数据一律 False → 预检会要求作者在正文里重新
框选一次（强制改成符合要求的）。

可逆：downgrade 直接删列。删掉不丢信息 —— 没标记过就是默认态 False。
"""

import sqlalchemy as sa
from alembic import op

revision = "b7e2c4a19f30"
down_revision = "a4e8b1c7d902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("template_spec_slots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "empty_text_from_anchor",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("template_spec_slots", schema=None) as batch_op:
        batch_op.drop_column("empty_text_from_anchor")
