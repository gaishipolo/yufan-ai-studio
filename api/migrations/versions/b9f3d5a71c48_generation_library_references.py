"""生成溯源快照加 `reference_library_ids`：v2 参考图终于有地方落。

`library_asset_generation.reference_asset_ids` 存的是**旧 `assets.id`**，只有
`library import-archive` 的历史导入会写它。而真正在跑的生成链路（生图 / 视频）
受理时拿的是 v2 资产 id：生图把它塞进快照字典的 `referenceLibraryIds`，任务那侧
塞进 `reference_asset_ids` —— 前者被 `_snapshot_values` 当「不认识的键」静默丢掉，
后者把 v2 id 写进了一列声明为 v1 的字段。两种都不对。

所以新开一列，两套 id 各归各处：v1 那列不动（历史证据），新写入进新列。

**不回填**：老行的 v2 参考图当年要么被丢掉、要么写进了 v1 那列，按现状回填等于
把「混进来的 v2 id」当成真数据抄一遍，反而让 v1 列看起来干净了。

downgrade 直接丢列。
"""

import sqlalchemy as sa
from alembic import op

revision = "b9f3d5a71c48"
down_revision = "a4d5e8c21f37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("library_asset_generation", schema=None) as batch_op:
        # `server_default` 只用在加列这一步：NOT NULL 的列在已有行上必须先有值，
        # 建完就去掉默认值，让「谁忘了传」在插入时暴露，而不是被 `[]` 蒙过去。
        batch_op.add_column(
            sa.Column(
                "reference_library_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    with op.batch_alter_table("library_asset_generation", schema=None) as batch_op:
        batch_op.alter_column("reference_library_ids", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("library_asset_generation", schema=None) as batch_op:
        batch_op.drop_column("reference_library_ids")
