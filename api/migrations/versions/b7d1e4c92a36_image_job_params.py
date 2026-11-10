"""image: 模型额外参数落库

Revision ID: b7d1e4c92a36
Revises: 5d8c2c96676c
Create Date: 2026-11-10 23:05:00.000000

生图入参不再只有 size / count：模型可以在 `param_schema` 里声明任意字段
（quality / seed / steps…），前端按它渲染表单，受理侧按它校验。这些值必须
跟着记录走，因为重试是**从库里重建** `GenerationRecord` 的（`_to_record`），
只存内存或只回落到默认值的话，「重试」会悄悄换成另一套参数重新提交，
而用户以为只是把同一张图再出一遍。

`size` / `count` 不并进来：历史列表、卡片角标、按尺寸统计都读独立列，
混进 JSON 会让取值退化成「解析失败就默认」，排序与统计也跟着失真。

老记录 `params_json = ''`（不是 `{}`）：空串表示「没声明额外参数」，
与「声明了但值全为空」区分得开，且不需要给存量行回填 JSON 解析。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d1e4c92a36"
down_revision: Union[str, Sequence[str], None] = "5d8c2c96676c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "image_jobs",
        sa.Column("params_json", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("image_jobs", "params_json")
