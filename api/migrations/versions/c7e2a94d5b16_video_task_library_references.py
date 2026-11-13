"""`video_tasks.reference_asset_ids` 改名 `reference_library_ids`。

这一列一直装的是 **v2 资产 id**：工作台与创建弹窗都是先 `POST /library/assets/upload`
拿到 id 再随请求下发，从来没人往里写过旧 `assets.id`。名字却写着 `asset_ids`，

于是两处同时错：

1. 架构守卫按 v1 口径要求任务模块声明一个 `AssetReferenceChecker`，任务因此一直
   挂着一个 `TaskAssetReferences`，查的是一列里根本不存在的 v1 id；
2. 真正的删除保护（v2 的 `purge`）一直缺着——删掉一张被视频任务引用的图，
   任务详情页的参考图直接裂，不可逆。

改名不是纯美容：列名决定守卫走哪一套判据。数据**原样保留**（同一个 id 空间，
前端写入的本来就是 v2 id）；万一有更早的行存了 v1 id，改名之后它们只是不再被
任何保护认领——与 v1 素材库一起下线，不回填、不猜测。

downgrade 把名字改回去（数据不动）。
"""

import sqlalchemy as sa
from alembic import op

revision = "c7e2a94d5b16"
down_revision = "b9f3d5a71c48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "reference_asset_ids",
            new_column_name="reference_library_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "reference_library_ids",
            new_column_name="reference_asset_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )
