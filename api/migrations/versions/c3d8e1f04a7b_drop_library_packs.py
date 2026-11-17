"""撤掉「素材封装(pack)」:表、资产行与模板里的封装槽位一并退场

Revision ID: c3d8e1f04a7b
Revises: b7c1e4a92f30
Create Date: 2026-11-16 22:40:00.000000

## 为什么撤

封装(pack)是把若干素材打成一个「整体」的容器:一行 `kind=pack` 的资产行(没有
自己的字节)+ `library_pack_items` 里的有序成员。模板封装里那种「一个槽位绑一
整个封装」的槽位类型,靠的也是它。

产品决定取消这个功能。它的问题不是不好用,而是**它让「一条素材」不再是唯一的
事实**:同一个成员既在个人库里、又在封装里;列表要决定「成员不再单列」(于是
出现了 `hidden_by_pack_clause` 这类只服务封装的可见性分支),下载要把封装再
展开一次,引用检查要多一个来源。取消之后,资产就是资产,少一层容器语义。

## 迁移做了什么

1. **删掉 `kind='pack'` 的资产行**。它们是空壳(没有自己的字节),删掉不影响
   任何素材字节 —— 成员仍然是各自的资产行。
   连带清掉它们身上的:标签绑定(`library_asset_tags`)、共享申请单
   (`library_share_requests`)、以及下载任务 `asset_ids` 里对它们的引用。
2. **删掉 `library_pack_items` 表**。
3. **模板里的封装槽位改成图片槽位并清空绑定**(`template_spec_slots` 与
   `template_specs.draft` 的 JSON 两处),并删掉 `template_spec_slots.pack_id`
   这根列 —— 模板不再有「封装槽位」这种类型。
   发布快照「只插入不更新」的原则在这里被破了一次:留着 `type='pack'` 会让这
   条数据在新代码里直接非法(槽位类型已经没有这个值),模板连编译都过不了。
   改成空的图片槽位后,模板还在、还能编辑,只是要重新挑一次素材。

## 降级

不可逆:封装相关的行与表都删掉了。回退代码需要先把封装功能重新做一遍,
数据回不来(成员素材本身没丢,它们是各自的资产行)。
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "c3d8e1f04a7b"
down_revision = "b7c1e4a92f30"
branch_labels = None
depends_on = None

#: 本迁移不可逆（见模块 docstring 的「降级」一节）。
#:
#: 这个常量是**机器可读的**判据：CI 的降级往返测试要算出「能回退到哪一道」，
#: 靠的就是扫描各迁移模块里的这个标记（`scripts/downgrade_anchor.py`）。
#: 新增不可逆迁移时**必须**把它带上，否则 CI 会在降级时撞上 `NotImplementedError`。
irreversible = True


def _pack_asset_ids(connection: sa.Connection) -> list[str]:
    rows = connection.execute(
        sa.text("SELECT id FROM library_assets WHERE kind = 'pack'")
    ).fetchall()
    return [row[0] for row in rows]


def upgrade() -> None:
    bind = op.get_bind()
    pack_ids = _pack_asset_ids(bind)
    if pack_ids:
        quoted = ", ".join(f"'{pid}'" for pid in pack_ids)
        bind.execute(
            sa.text(
                f"DELETE FROM library_asset_tags WHERE asset_id IN ({quoted})"
            )
        )
        bind.execute(
            sa.text(
                f"DELETE FROM library_share_requests WHERE asset_id IN ({quoted})"
            )
        )
        # 下载任务里记的是 id 列表(JSON):把封装 id 摘掉,剩下的成员照常进包。
        jobs = bind.execute(
            sa.text("SELECT id, asset_ids FROM library_download_jobs")
        ).fetchall()
        for job_id, raw in jobs:
            ids = raw if isinstance(raw, list) else json.loads(raw or "[]")
            if not any(pid in ids for pid in pack_ids):
                continue
            kept = [pid for pid in ids if pid not in pack_ids]
            bind.execute(
                sa.text(
                    "UPDATE library_download_jobs SET asset_ids = :ids WHERE id = :id"
                ),
                {"ids": json.dumps(kept), "id": job_id},
            )
        bind.execute(
            sa.text(f"DELETE FROM library_assets WHERE id IN ({quoted})")
        )

    # 模板的封装槽位 → 空的图片槽位(两处:草稿 JSON 与已发布的槽位行)
    bind.execute(
        sa.text(
            "UPDATE template_spec_slots SET type = 'image', asset_id = NULL, "
            "pack_id = NULL, min_count = 1, max_count = 1 WHERE type = 'pack'"
        )
    )
    specs = bind.execute(
        sa.text("SELECT id, draft FROM template_specs")
    ).fetchall()
    for spec_id, raw in specs:
        draft = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        slots = draft.get("slots")
        if not isinstance(slots, list):
            continue
        changed = False
        for slot in slots:
            if isinstance(slot, dict) and slot.get("type") == "pack":
                slot["type"] = "image"
                slot["assetId"] = None
                slot["packId"] = None
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE template_specs SET draft = :draft WHERE id = :id"),
                {"draft": json.dumps(draft), "id": spec_id},
            )

    # 槽位表里那根 `pack_id` 列也一并退场:模板不再有「封装槽位」这种类型
    op.drop_column("template_spec_slots", "pack_id")

    op.drop_table("library_pack_items")


def downgrade() -> None:
    # 不可逆:封装行与成员表都没了,退不回去。
    raise NotImplementedError("素材封装已下线,迁移不可回退")
