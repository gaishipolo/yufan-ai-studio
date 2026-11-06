"""asset: 去重约束限定在非归档来源

Revision ID: c1d9e4b7a260
Revises: a7c3f1b9d2e4
Create Date: 2026-11-06 15:10:00.000000

`uq_assets_owner_sha256` 原本是普通唯一约束，覆盖整张表。归档索引并入同一张表后
它变成了一个误伤：同一份文件「网页上传一次 + 归档登记一次」是两条真实记录
（两份物理副本、两套 URL、归属两个不同的库），却因为摘要相同被数据库拒掉。

去重是**上传**的语义（省一次写入、复用已有记录），归档登记是**登记已有文件**，
不该被上传规则约束。所以把约束改成带条件的唯一索引：

    UNIQUE (owner_id, sha256) WHERE source != 'archive'

SQLite 与 PostgreSQL 都支持部分索引。SQLite 不支持 `ALTER TABLE ... DROP CONSTRAINT`，
因此降级仍走 batch 重建表。

`sha256 IS NULL` 的行本来就不参与唯一索引（NULL 互不相同），语义不变。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d9e4b7a260"
down_revision: Union[str, Sequence[str], None] = "a7c3f1b9d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_assets_owner_sha256"
_WHERE = sa.text("source != 'archive'")


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_constraint(_INDEX, type_="unique")
    op.create_index(
        _INDEX,
        "assets",
        ["owner_id", "sha256"],
        unique=True,
        sqlite_where=_WHERE,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    # 降级会重新拒绝「同 owner 同摘要」的跨来源记录：先把归档侧的重复行摘要清空，
    # 否则建约束直接失败。只清摘要、不删行 —— 归档索引仍可重新导入，行本身丢了
    # 就意味着列表里少一条素材。
    op.execute(
        "UPDATE assets SET sha256 = NULL WHERE source = 'archive' AND EXISTS ("
        "  SELECT 1 FROM assets AS other WHERE other.owner_id = assets.owner_id"
        "  AND other.sha256 = assets.sha256 AND other.source != 'archive'"
        "  AND other.id != assets.id)"
    )
    op.drop_index(_INDEX, table_name="assets")
    with op.batch_alter_table("assets") as batch_op:
        batch_op.create_unique_constraint(_INDEX, ["owner_id", "sha256"])
