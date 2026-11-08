"""asset: 去重索引改成与代码口径一致，并让它真的生效

Revision ID: e4b7c2a91f38
Revises: df546efef239
Create Date: 2026-11-09 02:15:00.000000

`uq_assets_owner_sha256` 自 `c1d9e4b7a260` 起是：

    UNIQUE (owner_id, sha256) WHERE source != 'archive'

前提是「网页上传记 `upload`、盘上登记记 `archive`」。接口合并成一套 `/assets`
之后，`_ingest` 对两种入口**都写 `archive`**，于是这个条件对所有行都不成立——
索引里一行数据都没有，看着在防并发重复，实际从来没有约束过任何东西。

改成与 `ArchiveService._find_by_digest` 一字不差的口径：

    UNIQUE (owner_id, sha256, category) WHERE deleted_at IS NULL

三个差异各自的理由：

- 加 `category`：同一张图同时当人物参考和产品参考是正常用法，合并会误伤；
  旧索引没有这一列，一旦条件放开反而会把它们判成重复。
- 把 `source` 换成 `deleted_at`：「网页传的」和「盘上登记的」不再有区分意义，
  而回收站里的旧记录**不该**挡住「重新上传同一个文件」。
- 仍用部分索引：`sha256 IS NULL` 的行（算不出摘要）不参与，NULL 互不相同。

升级顺序是「先去重、再建索引」：生产上若有并发造成的重复行，直接建唯一索引
会失败，而失败发生在部署中途最难处理。重复行只标软删、不删数据——它们仍然
能从回收站看到并手动清理，字节也还有引用。

降级回到旧形态：先清空 `source = 'archive'` 的重复行摘要（与
`c1d9e4b7a260` 的降级同一套处理），否则新约束会拒掉现有数据。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7c2a91f38"
down_revision: Union[str, Sequence[str], None] = "df546efef239"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_assets_owner_sha256"
_NEW_WHERE = sa.text("deleted_at IS NULL")
_OLD_WHERE = sa.text("source != 'archive'")

# 同一 (owner, sha256, category) 只留最早的一条未删除行，其余标软删。
# 用 `created_at` 而不是 `MIN(id)`：id 是 uuid hex，与先后无关。
_DEDUPE = sa.text(
    "UPDATE assets SET deleted_at = CURRENT_TIMESTAMP"
    " WHERE deleted_at IS NULL AND sha256 IS NOT NULL AND category IS NOT NULL"
    " AND EXISTS ("
    "  SELECT 1 FROM assets AS other"
    "  WHERE other.owner_id = assets.owner_id"
    "    AND other.sha256 = assets.sha256"
    "    AND other.category = assets.category"
    "    AND other.deleted_at IS NULL"
    "    AND (other.created_at < assets.created_at"
    "         OR (other.created_at = assets.created_at AND other.id < assets.id))"
    " )"
)


def upgrade() -> None:
    op.execute(_DEDUPE)
    op.drop_index(_INDEX, table_name="assets")
    op.create_index(
        _INDEX,
        "assets",
        ["owner_id", "sha256", "category"],
        unique=True,
        sqlite_where=_NEW_WHERE,
        postgresql_where=_NEW_WHERE,
    )


def downgrade() -> None:
    # 旧约束覆盖整张表（除 archive 外），归档侧的跨来源重复行必须先让出摘要，
    # 否则建约束直接失败。只清摘要、不删行。
    op.execute(
        "UPDATE assets SET sha256 = NULL WHERE source = 'archive' AND EXISTS ("
        "  SELECT 1 FROM assets AS other WHERE other.owner_id = assets.owner_id"
        "  AND other.sha256 = assets.sha256 AND other.source != 'archive'"
        "  AND other.id != assets.id)"
    )
    op.drop_index(_INDEX, table_name="assets")
    op.create_index(
        _INDEX,
        "assets",
        ["owner_id", "sha256"],
        unique=True,
        sqlite_where=_OLD_WHERE,
        postgresql_where=_OLD_WHERE,
    )
