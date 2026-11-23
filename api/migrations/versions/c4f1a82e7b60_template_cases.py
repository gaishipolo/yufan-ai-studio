"""模板案例表（`template_cases`）。

一张新表，没有存量数据要回填。

## 为什么单独建表而不是塞进 `template_specs`

案例和模板是两个东西：模板是**活的**（有人持续编辑、发版），案例是**一次快照**
（发布那一刻的配置副本，发布后谁也不改它）。塞进 `template_specs` 只能靠一个
状态位区分，于是「模板被删」「案例被下架」「模板改名」「案例改名」四件事会挤进
同一张表的同一组列里 —— 每加一个字段都得先回答「这一列说的是模板还是案例」。

## 表里存什么

- `spec_id` / `spec_key` / `spec_version`：**回溯线索**，不参与复用逻辑；
- `config`：模板配置快照（camelCase JSON，与 `TemplateSpecIn` 同形）——
  复用者拿到的就是它，模板后来改了也不影响已发布的案例。

## 不建外键

与项目其它表一致（全项目零外键，靠字符串 id + 应用层校验）：删模板不会级联炸掉
案例，案例带着自己的快照照样能复用，只是界面上标一句「来源模板已删除」。
"""

import sqlalchemy as sa
from alembic import op

revision = "c4f1a82e7b60"
down_revision = "8d2a4f61c9e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "template_cases",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("spec_id", sa.String(length=32), nullable=False),
        sa.Column("spec_key", sa.String(length=80), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("offline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 列表页最常见的两种查询：按 owner 找「我发布的」、按 status 过滤可用案例。
    # 复合索引让「我的案例 + 状态」这条查询不用回表。
    op.create_index(
        "ix_template_cases_owner_status",
        "template_cases",
        ["owner_id", "status"],
    )
    op.create_index("ix_template_cases_category", "template_cases", ["category"])
    op.create_index("ix_template_cases_created_at", "template_cases", ["created_at"])
    op.create_index("ix_template_cases_owner_id", "template_cases", ["owner_id"])
    op.create_index("ix_template_cases_spec_id", "template_cases", ["spec_id"])
    op.create_index("ix_template_cases_status", "template_cases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_template_cases_status", table_name="template_cases")
    op.drop_index("ix_template_cases_spec_id", table_name="template_cases")
    op.drop_index("ix_template_cases_owner_id", table_name="template_cases")
    op.drop_index("ix_template_cases_created_at", table_name="template_cases")
    op.drop_index("ix_template_cases_category", table_name="template_cases")
    op.drop_index("ix_template_cases_owner_status", table_name="template_cases")
    op.drop_table("template_cases")
