"""下载任务的资产清单从 JSON 列拆成关联表

Revision ID: b7c4e91f2a58
Revises: c4f1a82e7b60
Create Date: 2026-11-23 18:40:00.000000

## 为什么拆

`library_download_jobs.asset_ids` 是 JSON 列，引用检查（「这条资产能不能彻底
删除」）靠 `LIKE '%"id"%'` 在 JSON **文本**里粗筛。那是「字符串里恰好出现了这个
id」，不是「这个 id 在清单里」——而这个判据的落点是**不可逆删除**：漏查 =
没人引用 = 删除放行 = 引用方的包裂开。

JSON 列还带来第二个问题：清单的**顺序**（包内 `001_名`、`002_名` 的编号）藏在
数组里，既没法用索引，也没法拉平成可 join 的行。

## 拆成什么样

新增 `library_download_job_assets(job_id, asset_id, position)`：

- `position` 保留打包顺序（用户勾选的顺序）；
- `ix_..._asset` 让引用检查走一次普通的 `IN` 查询，索引直接可用；
- 刻意**不加外键**（与 `library_pack_items` 同）：本模块的约定是「关系由应用层
  维护」，而引用检查要回答的正是「还引用着吗」——用外键会把它变成写入时刻的
  约束冲突，反而答不清。

`asset_count` 是**冗余计数**（任务的资产集合创建后不可变），与同一张表上既有的
`size_bytes` 同一做法：列表页显示「几个文件」不必每次回表数一遍。

## 数据搬迁

存量 `asset_ids` 逐行拆进关联表（`position` 按下标），并回填 `asset_count`，
然后删掉 JSON 列。

downgrade 反向：关联表 → JSON 列表 → 删关联表与计数列。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "b7c4e91f2a58"
down_revision = "c4f1a82e7b60"
branch_labels = None
depends_on = None


def _now(bind: Any) -> Any:
    """时间戳：**按方言给不同形状**，且与 ORM 写入的值逐字一致。

    不用数据库的 `now()`：sqlite 与 postgres 的时区行为不一致，交给应用层
    才能保证跨库格式统一（`db/base.py::TimestampMixin` 的说明）。

    两种形状的原因：

    - **sqlite**：ORM 走 SQLAlchemy 的 DATETIME 类型，写出的是「去掉 tzinfo
      的墙钟 + 微秒 6 位」（`2026-11-23 18:40:00.000000`）。直接传 datetime
      给 sqlite3 会走它的**默认 datetime adapter**——Python 3.12 起已弃用，
      而且写出的格式带 `+00:00` 后缀，与 ORM 的格式不一致（字符串排序会有
      偏差，读回来也依赖 `fromisoformat` 的宽松解析）。所以这里自己格式化。
    - **postgres**：`timestamptz` 需要带时区的值，aware datetime 才不会因
      会话时区而偏移。
    """
    moment = datetime.now(tz=UTC)
    if bind.dialect.name == "sqlite":
        return moment.strftime("%Y-%m-%d %H:%M:%S.%f")
    return moment


def _parse_asset_ids(raw: Any) -> list[str]:
    """把 `asset_ids` 列读成 id 列表。

    原生连接读出来的形态取决于方言与驱动：PG 的 json 类型给字符串、SQLite 的
    JSON 列也是 TEXT，但也可能已经是 list。逐个认。
    """
    if raw is None:
        return []
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def upgrade() -> None:
    op.add_column(
        "library_download_jobs",
        sa.Column("asset_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "library_download_job_assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_download_job_assets_created_at",
        "library_download_job_assets",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_library_download_job_assets_asset",
        "library_download_job_assets",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_library_download_job_assets_job",
        "library_download_job_assets",
        ["job_id", "position"],
        unique=False,
    )
    op.create_index(
        "uq_library_download_job_assets",
        "library_download_job_assets",
        ["job_id", "asset_id"],
        unique=True,
    )

    # 数据搬迁：JSON 列表 → 关联表行（position 按下标），并回填计数。
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, asset_ids FROM library_download_jobs")
    ).fetchall()
    for job_id, raw in rows:
        asset_ids = _parse_asset_ids(raw)
        for position, asset_id in enumerate(asset_ids):
            now = _now(bind)
            bind.execute(
                sa.text(
                    "INSERT INTO library_download_job_assets "
                    "(id, created_at, updated_at, job_id, asset_id, position) "
                    "VALUES (:id, :created_at, :updated_at, :job_id, :asset_id, :position)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "created_at": now,
                    "updated_at": now,
                    "job_id": job_id,
                    "asset_id": asset_id,
                    "position": position,
                },
            )
        bind.execute(
            sa.text(
                "UPDATE library_download_jobs SET asset_count = :n WHERE id = :job_id"
            ),
            {"n": len(asset_ids), "job_id": job_id},
        )

    # 删 JSON 列：sqlite 不支持直接 DROP COLUMN，用 batch 重建表。
    with op.batch_alter_table("library_download_jobs") as batch:
        batch.drop_column("asset_ids")


def downgrade() -> None:
    with op.batch_alter_table("library_download_jobs") as batch:
        batch.add_column(
            sa.Column(
                "asset_ids", sa.JSON(), nullable=False, server_default="[]"
            )
        )

    # 关联表 → JSON 列表（按 position 还原顺序）
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT job_id, asset_id FROM library_download_job_assets "
            "ORDER BY job_id, position"
        )
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for job_id, asset_id in rows:
        grouped.setdefault(job_id, []).append(asset_id)
    for job_id, asset_ids in grouped.items():
        bind.execute(
            sa.text(
                "UPDATE library_download_jobs SET asset_ids = :ids WHERE id = :job_id"
            ),
            {"ids": json.dumps(asset_ids), "job_id": job_id},
        )

    op.drop_index(
        "uq_library_download_job_assets",
        table_name="library_download_job_assets",
    )
    op.drop_index(
        "ix_library_download_job_assets_job",
        table_name="library_download_job_assets",
    )
    op.drop_index(
        "ix_library_download_job_assets_asset",
        table_name="library_download_job_assets",
    )
    op.drop_index(
        "ix_library_download_job_assets_created_at",
        table_name="library_download_job_assets",
    )
    op.drop_table("library_download_job_assets")

    with op.batch_alter_table("library_download_jobs") as batch:
        batch.drop_column("asset_count")
