"""彻底下线 v1 素材库：删 `assets` 表与全部 id 空间标记列

Revision ID: f5b2d8c4a173
Revises: d41c7a9e5f28
Create Date: 2026-11-13 21:10:00.000000

## 为什么这次是真删，不是降级成只读索引

`d41c7a9e5f28` 之后 v1 已经没有**活引用**了：两处决定「这个包现在还能不能生成」
的字段（`template_packs.cover_asset_id` / `template_pack_references.asset_id`）
都已改写成 v2 id。剩下的只是**历史证据**：

- `template_pack_versions.reference_ids`：冻结的发布快照（21 行 / 23 个 id）；
- `replacement_run_inputs.asset_id`：历史运行输入（148 行）。

上一版计划（§24.26）打算把 `assets` 留成只读 legacy 索引，好让这些历史行继续
解析得出素材名。**这个方案被否决了**：项目仍在开发阶段，历史任务的参考图展示
可以失效，而「两套 id 空间并存」的代价是每个下游都要判断「这个 id 属于谁」——
那正是这次要根除的东西。

所以删表之后：

- **新任务不受影响**：任何引用都已经只写 v2 id；
- **历史 run 的参考图解析不到**：`replacement_run_inputs` 里那 148 行的
  `asset_id` 会指向不存在的行，详情页按「素材已删除」降级展示。**这是明确
  接受的代价，不是漏改**；
- **冻结快照里那 23 个 id 同理**：`template_pack_versions` 是只增不改的历史
  证据，它记的是「当时用了哪几张图」，不是「现在还能打开哪几张图」。

## 一并删掉的「id 空间标记列」

并存期给几个表加过 `*_id_space` / `asset_id_space` 标记（`v1` / `v2` 显式分流）。
只剩一套 id 空间之后，这些列的语义是「恒等于某一侧」，留着只会让后来人以为
还有第二条路。`library_asset_generation.reference_asset_ids` 是同一批遗留里
唯一不是空间标记的那一列——它装的是 v1 id，v2 的等价列叫
`reference_library_ids`。

## 不可逆

被删的行没有备份，重建空表只会让降级看起来「成功」而实际丢数据。
所以这条迁移标了 `irreversible = True`，且 `downgrade()` 直接抛；
真要回到 v1 时代，正确动作是从备份恢复整个库。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5b2d8c4a173"
down_revision: Union[str, Sequence[str], None] = "d41c7a9e5f28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 本迁移不可逆：表结构可重建，被删的数据回不来（见模块 docstring 的「不可逆」一节）。
#: 机器可读判据：`scripts/downgrade_anchor.py` 靠它算 CI 的降级锚点。
irreversible = True

#: 要删掉的遗留列：(表, 列, 降级时还原的列类型)。
_COLUMNS = (
    ("template_packs", "cover_asset_id_space", sa.String(length=8)),
    ("template_pack_references", "asset_id_space", sa.String(length=8)),
    ("template_pack_versions", "reference_ids_space", sa.String(length=8)),
    ("replacement_run_inputs", "asset_id_space", sa.String(length=8)),
    ("video_templates", "source_video_asset_id_space", sa.String(length=8)),
    ("template_pack_templates", "source_video_id_space", sa.String(length=8)),
    ("template_pack_slots", "asset_id_space", sa.String(length=8)),
    ("library_asset_generation", "reference_asset_ids", sa.JSON()),
)


def upgrade() -> None:
    for table, column, _kind in _COLUMNS:
        # SQLite 的 DROP COLUMN 支持有限（老版本完全不支持），走 batch 才会重建表；
        # PG 上 batch 退化成普通 ALTER。
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(column)

    # 表上的索引与唯一约束由 `drop_table` 一并带走，不需要逐个 drop_index。
    op.drop_table("assets")


def downgrade() -> None:
    # 不可逆：表结构可重建，被删的数据回不来。
    raise NotImplementedError("v1 素材库已下线，删掉的行没有备份，迁移不可回退")
