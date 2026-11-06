"""asset: 统一素材索引（摘要 / 分类 / 来源 / 封面 key / 时长与音轨）

Revision ID: a7c3f1b9d2e4
Revises: f4c2d8e91b70
Create Date: 2026-11-06 14:30:00.000000

`assets` 从「个人素材的占位表」升级为**全部素材的唯一索引**：归档库不再靠
`catalog/assets/*.json` 当真相源，替换模板与素材页读同一张表。

- `sha256`：重复上传直接复用已有记录。**刻意允许 NULL**：唯一索引在 SQLite 与
  PostgreSQL 里都把多个 NULL 当作互不相同，历史归档行、算不出摘要的行因此能共存；
  换成空串做默认值，同 owner 的第二条空摘要就会撞唯一约束。
- `category`：归档分类（source / character / product / scene / audio），NULL = 未分类。
  上传的个人素材默认未分类，导入归档时按目录归类。
- `source`：upload / archive / generated。不参与鉴权，只回答「这条记录怎么来的」。
- `cover_key`：托管缩略图的对象 key。与既有 `cover_url`（远端封面）并存是刻意的，
  两者语义不同，出参按「有 key 用 key，否则退回 url」合成。
- `duration` / `has_audio`：归档媒体本来就带这两个字段，合并索引后不能丢。

新增列全部带 `server_default`，老行不需要回填；`sha256` / `category` 为 NULL
是**有效状态**而不是「待补」，所以没有回填脚本。

加列与加普通索引都用原生 DDL（SQLite 直接支持，不必重建表）。**只有唯一约束走
batch**：SQLite 不支持 `ALTER TABLE ... ADD CONSTRAINT`，Alembic 只能用 batch
重建表。它必须建成 UniqueConstraint 而不是 unique Index —— 模型里声明的是前者，
建成后者会让 `alembic check` 永远报「删索引 + 加约束」，下次 autogenerate 就会
生成一条破坏性迁移。表当前只有演示数据，重建代价可以接受。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3f1b9d2e4"
down_revision: Union[str, Sequence[str], None] = "f4c2d8e91b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("cover_key", sa.String(length=256), nullable=False, server_default=""),
    )
    op.add_column(
        "assets", sa.Column("duration", sa.Float(), nullable=False, server_default="0")
    )
    op.add_column(
        "assets",
        sa.Column("has_audio", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("assets", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("assets", sa.Column("category", sa.String(length=16), nullable=True))
    op.add_column(
        "assets",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="upload"),
    )

    op.create_index("ix_assets_category", "assets", ["category"], unique=False)
    # 与列表的排序键 (created_at desc, id desc) 同序，翻页不必再排序
    op.create_index(
        "ix_assets_owner_created", "assets", ["owner_id", "created_at"], unique=False
    )
    # 同 owner 内按内容去重；NULL 摘要（历史数据）不参与约束
    with op.batch_alter_table("assets") as batch_op:
        batch_op.create_unique_constraint(
            "uq_assets_owner_sha256", ["owner_id", "sha256"]
        )


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_constraint("uq_assets_owner_sha256", type_="unique")
    op.drop_index("ix_assets_owner_created", table_name="assets")
    op.drop_index("ix_assets_category", table_name="assets")

    op.drop_column("assets", "source")
    op.drop_column("assets", "category")
    op.drop_column("assets", "sha256")
    op.drop_column("assets", "has_audio")
    op.drop_column("assets", "duration")
    op.drop_column("assets", "cover_key")
