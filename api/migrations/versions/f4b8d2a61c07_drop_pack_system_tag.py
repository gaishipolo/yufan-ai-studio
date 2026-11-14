"""删掉「封装」这个系统标签：封装改成类型筛选里的一档

Revision ID: f4b8d2a61c07
Revises: c9a1f4e27b53
Create Date: 2026-11-14 18:40:00.000000

## 为什么删

「这是个封装」是 `library_assets.kind = 'pack'` 的事实，不是用户词表里的一个词。
当初拿标签代替筛选项，理由写在 `tags.PACK_TAG_NAME` 旁边：「类型筛选里没有
『封装』这一档，靠它找封装」。现在类型筛选有这一档，这个标签就成了**第二份
事实来源**，而且它的失效方式很难看：标签是用户可编辑的，卡片上的 × 一点，
这个封装在「图片 / 视频 / 音频 / 文本」里都筛不到，只剩「全部」里能翻到 ——
而它恰恰是那些素材唯一的容器。

## 删哪些

`library_tags` 里名为「封装」的**一级**标签及其绑定（`library_asset_tags`）。
系统只建这一种，且从不给它加子级（两级守卫要求显式 move）。

带子级的不动：那说明它已经被演化成用户自己的词表结构，删掉会让子标签指向
一个不存在的父级。宁可留一个多余的词，也不制造悬空 parent_id。

`library_assets` 一行不改 —— 封装的 `kind` 本来就是 `pack`，过去现在将来都是。

## 降级

绑定关系删了就回不来，降级只重建一条**空**标签，等于在标签栏里留一个「0 条」
的词，比不恢复更让人困惑。所以这里明确什么都不做。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b8d2a61c07"
down_revision: Union[str, Sequence[str], None] = "c9a1f4e27b53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: 系统标签名。写死在迁移里而不是 import 业务常量：迁移是历史快照，
#: 常量以后改名不该让这条已经跑过的迁移换行为。
_LEGACY_PACK_TAG = "封装"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id FROM library_tags WHERE name = :name AND parent_id = ''"
        ),
        {"name": _LEGACY_PACK_TAG},
    ).fetchall()
    for (tag_id,) in rows:
        has_child = bind.execute(
            sa.text("SELECT 1 FROM library_tags WHERE parent_id = :id LIMIT 1"),
            {"id": tag_id},
        ).first()
        if has_child:
            continue
        bind.execute(
            sa.text("DELETE FROM library_asset_tags WHERE tag_id = :id"), {"id": tag_id}
        )
        bind.execute(sa.text("DELETE FROM library_tags WHERE id = :id"), {"id": tag_id})


def downgrade() -> None:
    """不恢复：绑定关系已经没了，重建一条空标签只会在标签栏里多一个 0 条的词。"""
