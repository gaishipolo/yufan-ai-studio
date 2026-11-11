"""Version image reference images.

生图记录补一列参考图快照：图生图 / 多图合成要把素材的字节内联进提交请求，
而重试是从这条记录重建请求的——不落库，重试就静默退化成纯文生图。

列可空性跟 `params_json` 一致（`NOT NULL` + 空串表示「没有参考图」），
不用 `nullable=True`：读路径要区分「空」和「不知道」，少一个状态少一处分支。
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e8c1d6f920"
down_revision = "a9c2e5f8b310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column("reference_json", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("image_jobs", "reference_json")
