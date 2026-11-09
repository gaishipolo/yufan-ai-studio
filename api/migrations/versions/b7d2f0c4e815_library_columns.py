"""asset: 字节表、分类表与库归属列（M1-B / M1-D / M1-E）

Revision ID: b7d2f0c4e815
Revises: a1c4e8b72d90
Create Date: 2026-11-09 03:55:00.000000

这一版把「一条记录同时是字节容器、归属记录、权限载体」拆开：

- `blobs`：字节事实。`sha256` 唯一，素材记录可以有多条指向它。
- `categories`：内容组织。每个库一棵树（`scope` + `owner_key`）。
- `assets.visibility` / `team_id`：库边界。
- `assets.media_type`：系统判定的媒体类型，值域多一个 `document`。

## 回填策略：迁移只做「可以一次算完」的部分

分类树与 `category_id` 在这里回填（纯映射，不依赖外部服务）；
**blob 回填不在这里**——它要读文件、算摘要、写存储，跑在部署事务里会让
一次发布卡住好几分钟，失败后还要靠手动清理。那部分走
`app assets backfill-blobs`（幂等、分批、可重入），doctor 会盯着未回填条数。

读路径在 `blob_id` 为空时回落 `storage_key` / `size_bytes` / `sha256`，
所以「迁移完成但回填没跑」是一个**可读的中间态**，不是坏状态。

## 分类树的映射口径

旧 `category` 同时承载「业务类型」和「目录」两种语义，这里按用途归一：

| 旧值 | 新分类 | 说明 |
| --- | --- | --- |
| `source` | 参考素材 | 原片（视频） |
| `character` | 人物形象 | |
| `product` | 产品图 | |
| `scene` | 场景背景 | |
| `audio` | **参考素材** | 音频不是用途，媒体类型分面为 `audio` |
| `NULL` | 未分类收件箱 | |

`audio → 参考素材` 是这里唯一需要人复核的映射：把音频塞进某个用途分类
会造出一个不存在的概念。发布前导出一份音频清单过一眼。

## 降级

删列、删表。`visibility` 的数据会丢，但旧的 `shared` 列仍在（M1-G 才删），
所以「谁公开过」这条事实没有损失。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2f0c4e815"
down_revision: Union[str, Sequence[str], None] = "a1c4e8b72d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 每个库初始的用途分类。顺序就是分类条的默认顺序（可被用户拖动改 sort_order）。
_PURPOSES: tuple[str, ...] = ("参考素材", "产品图", "人物形象", "场景背景", "合成成片")
_INBOX = "未分类收件箱"

# 旧枚举 → 新分类名。`audio` 归到参考素材，理由见模块文档。
_LEGACY_PURPOSE: dict[str, str] = {
    "source": "参考素材",
    "character": "人物形象",
    "product": "产品图",
    "scene": "场景背景",
    "audio": "参考素材",
}

# 轻量表定义只给 `bulk_insert` 用：迁移里不能用 ORM 模型（版本会演进，
# 老迁移引用新模型 = 未来某次重跑直接崩）。
_CATEGORIES = sa.table(
    "categories",
    sa.column("id", sa.String),
    sa.column("scope", sa.String),
    sa.column("owner_key", sa.String),
    sa.column("name", sa.String),
    sa.column("kind", sa.String),
    sa.column("is_inbox", sa.Boolean),
    sa.column("sort_order", sa.Integer),
    sa.column("archived_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        "blobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False),
        sa.Column("preview_key", sa.String(length=256), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("ref_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blobs")),
    )
    op.create_index(
        "uq_blobs_sha256",
        "blobs",
        ["sha256"],
        unique=True,
        sqlite_where=sa.text("sha256 IS NOT NULL"),
        postgresql_where=sa.text("sha256 IS NOT NULL"),
    )
    op.create_index(
        "ix_blobs_expiring",
        "blobs",
        ["tier", "expires_at"],
        unique=False,
        sqlite_where=sa.text("expires_at IS NOT NULL"),
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("owner_key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("is_inbox", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
    )
    op.create_index(
        "uq_categories_tree_name",
        "categories",
        ["scope", "owner_key", "name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    # `is_inbox` 在 SQLite 是整数、在 PG 是布尔，WHERE 子句必须分方言写。
    # 写死 `= 1` 在 PG 上会直接报「operator does not exist: boolean = integer」。
    inbox_where = (
        sa.text("is_inbox = 1") if op.get_bind().dialect.name == "sqlite" else sa.text("is_inbox")
    )
    op.create_index(
        "uq_categories_inbox",
        "categories",
        ["scope", "owner_key"],
        unique=True,
        sqlite_where=inbox_where,
        postgresql_where=inbox_where,
    )
    op.create_index(
        "ix_categories_tree", "categories", ["scope", "owner_key", "sort_order"], unique=False
    )

    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("blob_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("category_id", sa.String(length=32), nullable=True))
        # NOT NULL 列加 `server_default`：PG 不允许给已有数据的表加无默认值的
        # NOT NULL 列。回填完 `shared=true` 的行之后立刻把默认值摘掉——
        # 留着它会让「忘了写 visibility」的新行静默变成 personal。
        batch_op.add_column(
            sa.Column("visibility", sa.String(length=16), nullable=False, server_default="personal")
        )
        batch_op.add_column(sa.Column("team_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("media_type", sa.String(length=16), nullable=True))
        batch_op.create_index(batch_op.f("ix_assets_blob_id"), ["blob_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_category_id"), ["category_id"], unique=False)
        batch_op.create_index(
            "ix_assets_library", ["visibility", "team_id", "created_at"], unique=False
        )
        batch_op.create_index(
            "ix_assets_category_created", ["category_id", "created_at"], unique=False
        )

    bind = op.get_bind()

    # 1) 可见域：曾经 `shared=true` 的就是今天公共库里的东西。
    #    不猜团队归属——存量素材当初只属于个人，猜错无从发现。
    bind.execute(sa.text("UPDATE assets SET visibility = 'brand' WHERE shared = :shared"), {"shared": True})

    # 2) 媒体类型：按 mime 重算，不信历史 `type`（它可能被手工改过）。
    bind.execute(
        sa.text(
            "UPDATE assets SET media_type = CASE "
            "WHEN mime_type LIKE 'video/%' THEN 'video' "
            "WHEN mime_type LIKE 'audio/%' THEN 'audio' "
            "WHEN mime_type LIKE 'image/%' THEN 'image' "
            "ELSE 'document' END"
        )
    )

    # 3) 分类树：公共库一棵 + 每个出现过的个人库各一棵 + 已有团队各一棵。
    now = datetime.now(UTC)
    trees: list[tuple[str, str]] = [("brand", "")]
    owners = [
        row[0]
        for row in bind.execute(sa.text("SELECT DISTINCT owner_id FROM assets")).all()
        if row[0]
    ]
    trees += [("personal", owner) for owner in owners]
    team_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM teams")).all() if row[0]]
    trees += [("team", team_id) for team_id in team_ids]

    tree_ids: dict[tuple[str, str], dict[str, str]] = {}
    rows: list[dict[str, object]] = []
    for scope, owner_key in trees:
        by_name: dict[str, str] = {}
        inbox_id = uuid.uuid4().hex
        by_name[_INBOX] = inbox_id
        rows.append(
            {
                "id": inbox_id,
                "scope": scope,
                "owner_key": owner_key,
                "name": _INBOX,
                "kind": "system",
                "is_inbox": True,
                "sort_order": 0,
                "archived_at": None,
                "created_at": now,
            }
        )
        for index, name in enumerate(_PURPOSES, start=1):
            category_id = uuid.uuid4().hex
            by_name[name] = category_id
            rows.append(
                {
                    "id": category_id,
                    "scope": scope,
                    "owner_key": owner_key,
                    "name": name,
                    "kind": "system",
                    "is_inbox": False,
                    "sort_order": index * 10,
                    "archived_at": None,
                    "created_at": now,
                }
            )
        tree_ids[(scope, owner_key)] = by_name
    if rows:
        op.bulk_insert(_CATEGORIES, rows)

    # 4) 回填 `category_id`：brand 库一棵树，personal 按 owner 各一棵。
    legacy = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT DISTINCT category FROM assets WHERE category IS NOT NULL")
        ).all()
        if row[0]
    ]
    for old in legacy:
        purpose = _LEGACY_PURPOSE.get(old)
        if purpose is None:
            continue
        target = tree_ids[("brand", "")][purpose]
        bind.execute(
            sa.text(
                "UPDATE assets SET category_id = :cid "
                "WHERE category = :old AND visibility = 'brand'"
            ),
            {"cid": target, "old": old},
        )
        for owner in owners:
            bind.execute(
                sa.text(
                    "UPDATE assets SET category_id = :cid "
                    "WHERE category = :old AND owner_id = :owner AND visibility = 'personal'"
                ),
                {"cid": tree_ids[("personal", owner)][purpose], "old": old, "owner": owner},
            )

    # 未分类：落到各自库的收件箱，而不是留 NULL——「只属于一个分类」是强不变量，
    # 用 NULL 会让它退化成「零个或一个」（设计 §2.4）。
    bind.execute(
        sa.text(
            "UPDATE assets SET category_id = :cid WHERE category_id IS NULL AND visibility = 'brand'"
        ),
        {"cid": tree_ids[("brand", "")][_INBOX]},
    )
    for owner in owners:
        bind.execute(
            sa.text(
                "UPDATE assets SET category_id = :cid "
                "WHERE category_id IS NULL AND owner_id = :owner AND visibility = 'personal'"
            ),
            {"cid": tree_ids[("personal", owner)][_INBOX], "owner": owner},
        )

    # 5) 摘掉临时默认值：之后「没写 visibility」应当是显式失败，不是静默 personal。
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.alter_column("visibility", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index("ix_assets_category_created")
        batch_op.drop_index("ix_assets_library")
        batch_op.drop_index(batch_op.f("ix_assets_category_id"))
        batch_op.drop_index(batch_op.f("ix_assets_blob_id"))
        batch_op.drop_column("media_type")
        batch_op.drop_column("team_id")
        batch_op.drop_column("visibility")
        batch_op.drop_column("category_id")
        batch_op.drop_column("blob_id")

    op.drop_index("ix_categories_tree", table_name="categories")
    op.drop_index("uq_categories_inbox", table_name="categories")
    op.drop_index("uq_categories_tree_name", table_name="categories")
    op.drop_table("categories")

    op.drop_index("ix_blobs_expiring", table_name="blobs")
    op.drop_index("uq_blobs_sha256", table_name="blobs")
    op.drop_table("blobs")
