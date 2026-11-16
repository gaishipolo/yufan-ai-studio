"""新增 `library_assets.purpose`：把「展示案例」变成资产的一个正式用途

Revision ID: b7c1e4a92f30
Revises: e6a1b3c8d045
Create Date: 2026-11-16 20:10:00.000000

## 为什么改

模板的展示案例（`template_specs.draft.demoAssetId`）一直指向作者**个人**库里
的一条普通资产。个人可见的后果是：换一个账号打开模板库，那条素材读不到
（`GET /library/assets/{id}` 直接 404），前端判不出它是视频，封面和悬停播放
一起消失 —— 卡片退化成一片渐变，没有任何提示。

案例需要的是「所有人可见，但只有持权限的人能浏览与管理」。这既不是
`personal`，也不该混进团队库，所以先给资产加一个用途标记：

- `purpose=''`：普通素材，行为完全不变；
- `purpose='case'`：案例。对任何登录用户可读（模板卡片要能播），
  进案例池需要 `library:case:read`，上传/移入需要 `library:case:write`。

**没有复用 `kind`**：`kind` 在这套代码里是媒体类型（上传按 mime 反推、预览按它
选生成器、`/stream` 按 `kind == 'video'` 判分段、去重唯一索引也把它算进键）。
把 `case` 塞进 `kind` 会丢掉图/视频的区分，而那是封面的全部依据。

## 迁移什么

1. 加列 `purpose`（默认空串，存量行不动）；
2. 加索引 `(purpose, state, created_at)`：案例池是唯一按它过滤的查询；
3. 回填：把**当前被已发布模板当作案例引用**的资产标成 `case`。

回填按草稿 JSON 里的 `demoAssetId` 找，`deleted_at` 为空的模板全算——与
`SpecDemoCases` 引用检查同一口径。找不到对应资产的 id 直接跳过（悬空指针由
引用检查负责暴露，迁移不该在这里删东西）。

## 降级

删列 + 删索引即可；`purpose` 是纯标记，没有它普通素材的行为与迁移前一致。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1e4a92f30"
down_revision: str | None = "e6a1b3c8d045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_assets",
        sa.Column("purpose", sa.String(length=16), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_library_assets_purpose_state",
        "library_assets",
        ["purpose", "state", "created_at"],
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT draft FROM template_specs WHERE deleted_at IS NULL AND draft IS NOT NULL")
    ).fetchall()
    asset_ids: set[str] = set()
    for (draft,) in rows:
        # sqlite 给的是字符串，PG 的 JSON 列由驱动直接解成 dict —— 两种都要认。
        payload = json.loads(draft) if isinstance(draft, str) else draft
        if not isinstance(payload, dict):
            continue
        demo_id = payload.get("demoAssetId") or payload.get("demo_asset_id")
        if isinstance(demo_id, str) and demo_id:
            asset_ids.add(demo_id)
    for asset_id in sorted(asset_ids):
        bind.execute(
            sa.text("UPDATE library_assets SET purpose = 'case' WHERE id = :asset_id"),
            {"asset_id": asset_id},
        )


def downgrade() -> None:
    op.drop_index("ix_library_assets_purpose_state", table_name="library_assets")
    with op.batch_alter_table("library_assets") as batch:
        batch.drop_column("purpose")
