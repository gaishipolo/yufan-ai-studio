"""asset: 生成配方、血缘引用与候选生命周期（M3-A）

Revision ID: e5b2c8d4f731
Revises: d1a7b3e5c924
Create Date: 2026-11-09 07:40:00.000000

## 三件事一起上

| 对象 | 为什么不能拆开 |
| --- | --- |
| `generation_recipes` | 没有它，`assets.recipe_id` 就没有指向 |
| `asset_refs` | 没有它，"这张图被哪些生成用过"只能全表扫 JSON |
| `assets` 的 8 个新列 | 没有它们，候选与成品的区别无处存放 |

## 存量行的归类是**唯一正确答案**，不是折中

所有历史素材都是人上传/归档导入的：`origin='upload'`、`stage='adopted'`、
`compliance_status='approved'`。这三条不是"先给个默认值以后再改"——
它们就是事实：审核（B2）只针对 AI 产物，历史素材没有被审核过也不需要。

`compliance_status` 表级默认值刻意是 `pending`（见 `models.py` 的说明），
所以这里必须显式回填，且回填后**摘掉临时 server_default**：
留着它，新建行漏写合规状态就会静默变成 `approved`。

## 关于 blob 的桶

`blobs.tier` 是派生值（有 `adopted` → `final`）。存量素材全部是 `adopted`，
而 `blobs.tier` 的列默认值就是 `final`，所以这一版**不需要回填**。
新增的候选会在入库时写 `draft` + `expires_at`。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b2c8d4f731"
down_revision: str | None = "d1a7b3e5c924"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "generation_recipes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("prompt_language", sa.String(8), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("seed", sa.String(64), nullable=False),
        sa.Column("seed_requested", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=False),
        sa.Column("template_id", sa.String(32), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("subject_version", sa.Integer(), nullable=False),
        sa.Column("content_label", sa.JSON(), nullable=False),
        sa.Column("parent_recipe_id", sa.String(32), nullable=True),
        sa.Column("reference_asset_ids", sa.JSON(), nullable=False),
        sa.Column("cost_units", sa.Float(), nullable=False),
        sa.Column("created_by", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_generation_recipes_owner", "generation_recipes",
                    ["created_by", "created_at"])
    op.create_index("ix_generation_recipes_parent", "generation_recipes", ["parent_recipe_id"])
    op.create_index("ix_generation_recipes_task", "generation_recipes", ["task_id"])
    op.create_index("ix_generation_recipes_template", "generation_recipes",
                    ["template_id", "template_version"])

    op.create_table(
        "asset_refs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("recipe_id", sa.String(32), nullable=False),
        sa.Column("asset_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        # 同一次生成里同一张图只算一条：重复会让"用了 3 张参考图"里出现 5 行，
        # 而提示词里只有一个 reference-1
        sa.UniqueConstraint("recipe_id", "asset_id", name="uq_asset_refs_pair"),
    )
    op.create_index("ix_asset_refs_recipe_id", "asset_refs", ["recipe_id"])
    op.create_index("ix_asset_refs_asset_id", "asset_refs", ["asset_id"])
    # 反查的主形状：先按素材筛，再取配方
    op.create_index("ix_asset_refs_asset", "asset_refs", ["asset_id", "recipe_id"])

    # 加列：NOT NULL 的三列必须带临时 server_default，否则 PG 拒绝给已有数据的表加列
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("recipe_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("reviewed_by", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="upload")
        )
        batch_op.add_column(
            sa.Column("stage", sa.String(length=16), nullable=False, server_default="adopted")
        )
        batch_op.add_column(
            sa.Column(
                "compliance_status", sa.String(length=16), nullable=False, server_default="approved"
            )
        )
        batch_op.add_column(
            sa.Column("compliance_note", sa.Text(), nullable=False, server_default="")
        )
        batch_op.create_index(batch_op.f("ix_assets_recipe_id"), ["recipe_id"], unique=False)
        batch_op.create_index(
            "ix_assets_owner_stage", ["owner_id", "stage", "created_at"], unique=False
        )

    # 显式回填一次：ADD COLUMN 的 default 已经填过，但这条 UPDATE 让"这些行是
    # 人上传的"成为迁移自己的声明，而不是依赖某条 SQL 的副作用
    op.get_bind().execute(
        sa.text(
            "UPDATE assets SET origin = 'upload', stage = 'adopted',"
            " compliance_status = 'approved'"
        )
    )

    # 摘掉临时默认值：之后漏写合规状态就该是显式失败，不是静默通过
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.alter_column("origin", server_default=None)
        batch_op.alter_column("stage", server_default=None)
        batch_op.alter_column("compliance_status", server_default=None)
        batch_op.alter_column("compliance_note", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index("ix_assets_owner_stage")
        batch_op.drop_index(batch_op.f("ix_assets_recipe_id"))
        batch_op.drop_column("compliance_note")
        batch_op.drop_column("compliance_status")
        batch_op.drop_column("stage")
        batch_op.drop_column("origin")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("adopted_at")
        batch_op.drop_column("recipe_id")

    op.drop_index("ix_asset_refs_asset", table_name="asset_refs")
    op.drop_index("ix_asset_refs_asset_id", table_name="asset_refs")
    op.drop_index("ix_asset_refs_recipe_id", table_name="asset_refs")
    op.drop_table("asset_refs")

    op.drop_index("ix_generation_recipes_template", table_name="generation_recipes")
    op.drop_index("ix_generation_recipes_task", table_name="generation_recipes")
    op.drop_index("ix_generation_recipes_parent", table_name="generation_recipes")
    op.drop_index("ix_generation_recipes_owner", table_name="generation_recipes")
    op.drop_table("generation_recipes")
