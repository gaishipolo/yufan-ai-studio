"""视频模板的原片 id 也加「空间标记」列。

`c3a8f1d62b74` 给四列加了空间标记，漏了 `video_templates` —— 那时这张表还不存在
（它是步 2 的新表，见 `e5b91c4d7a26`）。步 2 的导入把 `catalog.json` 里的 v1 id
原样写进 `source_video_asset_id`，而切换后新封装上传的参考视频是 v2 id：
不标空间就只能靠「查不到再回退另一侧」这种隐式兜底，而
`core/ports/library_lookup.py:8-10` 明确禁止那么做。

形状与 `c3a8f1d62b74` 一致：先 `nullable=True` → 回填 `v1` → 再改 `nullable=False`。
**不批量改写 id 本身**：`catalog.json` 里的是历史证据，改写它会让已封装的模板
指不到原片。

downgrade 直接丢列（可重建：回填 `v1` 就是唯一初始值）。
"""

import sqlalchemy as sa
from alembic import op

revision = "f2d7a91c4b83"
down_revision = "e5b91c4d7a26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_templates", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_video_asset_id_space", sa.String(length=4), nullable=True)
        )
    # 建表以来所有行都来自 `catalog.json` 或 v1 素材接口：这一刻的真相就是 `v1`。
    op.execute(
        "UPDATE video_templates SET source_video_asset_id_space = 'v1' "
        "WHERE source_video_asset_id_space IS NULL"
    )
    with op.batch_alter_table("video_templates", schema=None) as batch_op:
        batch_op.alter_column(
            "source_video_asset_id_space",
            existing_type=sa.String(length=4),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("video_templates", schema=None) as batch_op:
        batch_op.drop_column("source_video_asset_id_space")
