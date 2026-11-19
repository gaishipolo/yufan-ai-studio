"""清掉回收站里两个多值槽位的 e2e 模板，为一槽一张扫清历史数据

Revision ID: 4e06879f9f75
Revises: d7f2a91c48b3
Create Date: 2026-11-19 15:05:00.000000

## 为什么删而不是改

模板封装锁死"一个槽位一张素材"。库里原本有 3 个 `max_count=2` 的槽位，
全部属于 `e2e-combo-multi` / `e2e-combo-video` 两个 e2e 测试模板，且这两个
模板在 2026-11-17 已被软删进回收站（`deleted_at` 有值）—— 是废弃的测试数据，
不是用户资产。

把它们的多值槽位"改"成一张会留下两个内容被悄悄改过的假模板（提示词里的
`{{slot:hero*}}` 也要跟着改），不如直接彻底删除。全库扫描确认没有任何任务
记录或其它表引用这两个 spec_key，删除是安全的。

删后全库不再有 `max_count>1` 的槽位，编译层也随之收紧为真·一槽一张。

## 不可逆

物理删除 head / versions / slots 三层数据，降级无法还原（`downgrade` 明确
不做事）。因此标 `irreversible = True`，CI 降级往返会停在更早的锚点。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "4e06879f9f75"
down_revision: Union[str, None] = "d7f2a91c48b3"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

#: 物理删除不可还原。
irreversible = True

_SPEC_KEYS = ("e2e-combo-multi", "e2e-combo-video")


def upgrade() -> None:
    bind = op.get_bind()
    # 级联顺序：槽位 → 版本 → head。不用 FK ON DELETE CASCADE 是显式表达意图。
    bind.execute(
        sa.text(
            "DELETE FROM template_spec_slots WHERE version_id IN ("
            "  SELECT v.id FROM template_spec_versions v"
            "  JOIN template_specs s ON s.id = v.spec_id"
            "  WHERE s.spec_key IN (:k1, :k2)"
            ")",
        ),
        {"k1": _SPEC_KEYS[0], "k2": _SPEC_KEYS[1]},
    )
    bind.execute(
        sa.text(
            "DELETE FROM template_spec_versions WHERE spec_id IN ("
            "  SELECT id FROM template_specs WHERE spec_key IN (:k1, :k2)"
            ")",
        ),
        {"k1": _SPEC_KEYS[0], "k2": _SPEC_KEYS[1]},
    )
    bind.execute(
        sa.text("DELETE FROM template_specs WHERE spec_key IN (:k1, :k2)"),
        {"k1": _SPEC_KEYS[0], "k2": _SPEC_KEYS[1]},
    )


def downgrade() -> None:
    # 物理删除的 e2e 测试数据无法还原；明确不做事，而不是重建空壳。
    pass
