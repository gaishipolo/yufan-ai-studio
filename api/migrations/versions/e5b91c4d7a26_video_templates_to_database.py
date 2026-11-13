"""视频模板目录从 `catalog.json` 搬到表（问题 A）。

`modules/template/video_template_store.py` 原来把「视频模板目录」存成
`api/var/video_templates/catalog.json`，全量读 → 改 → 全量写，靠一把**进程内**
`threading.Lock` 互斥。文件头写的「不用表」理由是部署库的 `alembic_version`
指向迁移链里不存在的 revision（`b4e2d8f1a630`）——那个 revision 现在就在链上
（`b4e2d8f1a630_user_contact_fields.py`），`alembic current` 就是 head。
所以这不是引入新东西，是把当初注释里的 TODO 做掉。

三处「自己手搓」的替代关系：

| 原实现 | 现在 |
|---|---|
| `_LOCK` 保护读改写 | 数据库事务 |
| 写前整份复制、滚动留 20 份 | 表本身（每行独立） |
| `_LOCK` 里「抢占固定蓝本」 | `uq_video_templates_pinned` 部分唯一索引 |

**`pinned` 的部分唯一索引**是这次迁移唯一需要解释的一行：`WHERE pinned` 在
PostgreSQL 与 SQLite 上都合法（后者把非 0 当真），于是「全库只有一个固定蓝本」
从「应用层先清后设」变成数据库约束——多 worker 并发时后者会被拒绝而不是静默覆盖。

**数据搬迁不在这里做**：历史内容在部署机的 `catalog.json` 里，迁移脚本跑在
数据库进程里读不到它，硬塞一条 `op.get_bind()` 读文件的逻辑会让 `upgrade`
依赖运维机器的目录布局。用 `app import-video-templates` 一次性导入（按 `id` 幂等），
`catalog.json` 保留只读，观察一轮再清理。

downgrade 只 drop 这张新表，不碰其他表。
"""

import sqlalchemy as sa
from alembic import op

revision = "e5b91c4d7a26"
down_revision = "c3a8f1d62b74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_templates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_video_asset_id", sa.String(length=32), nullable=True),
        sa.Column("source_video_path", sa.String(length=512), nullable=False),
        sa.Column("source_video_name", sa.String(length=200), nullable=False),
        sa.Column("source_duration_sec", sa.Float(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("pack_id", sa.String(length=32), nullable=True),
        sa.Column("pack_name", sa.String(length=160), nullable=False),
        sa.Column("pack_version", sa.Integer(), nullable=False),
        sa.Column("default_kind", sa.String(length=16), nullable=False),
        sa.Column("media_order", sa.JSON(), nullable=False),
        sa.Column("generation", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("cover_url", sa.String(length=512), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_templates")),
        sa.UniqueConstraint("template_key", name=op.f("uq_video_templates_template_key")),
    )
    # `created_at` 的索引来自 `TimestampMixin(index=True)`，迁移里要显式建，
    # 否则 `compare_metadata` 会报「模型有、库没有」。
    op.create_index(
        op.f("ix_video_templates_created_at"), "video_templates", ["created_at"]
    )
    op.create_index(
        "ix_video_templates_source_kind",
        "video_templates",
        ["source_video_asset_id", "default_kind"],
    )
    # 部分唯一索引 = 「全库只有一个固定蓝本」。两个库的 WHERE 写法都能写成
    # `pinned`：SQLite 把非 0 当真，PostgreSQL 直接吃布尔列。
    op.create_index(
        "uq_video_templates_pinned",
        "video_templates",
        ["pinned"],
        unique=True,
        sqlite_where=sa.text("pinned"),
        postgresql_where=sa.text("pinned"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_video_templates_pinned",
        table_name="video_templates",
        sqlite_where=sa.text("pinned"),
        postgresql_where=sa.text("pinned"),
    )
    op.drop_index("ix_video_templates_source_kind", table_name="video_templates")
    op.drop_index(op.f("ix_video_templates_created_at"), table_name="video_templates")
    op.drop_table("video_templates")
