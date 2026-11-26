"""image_jobs.name 放宽到 200。

生图作业名这一列原来只有 120 字，而模板库把**模板名**（上限 160 字）原样当任务名
传 —— 模板能发布、填表能编译，点「生成」才在提交阶段被 422 打回；同一模板走视频
却是好的（视频任务标题上限 200）。两个上限各写一个数字，漂移就出在这里。

列宽跟着放宽到 200（与 `video_tasks.title` 一致），列宽和接口上限对齐，
生图与视频两条路的卡片名字才会一模一样。

downgrade 会截断超长行：`String(120)` 装不下一条 160 字的模板名，不截断就降不
回去。用 `substr` 是因为它在 PG 与 SQLite 上同义。
"""

import sqlalchemy as sa
from alembic import op

revision = "a4e8b1c7d902"
down_revision = "e5a91c3f7b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=120),
            type_=sa.String(length=200),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE image_jobs SET name = substr(name, 1, 120) WHERE length(name) > 120"
    )
    with op.batch_alter_table("image_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=200),
            type_=sa.String(length=120),
            existing_nullable=False,
        )
