"""v1 素材引用切到资产库 v2：只改写**活引用**，冻结快照一律不动

Revision ID: d41c7a9e5f28
Revises: b7e3d9f2a418
Create Date: 2026-11-13 17:40:00.000000

## 改写什么

两处**当前生效**的引用，它们决定「这个包现在还能不能生成」：

- `template_packs.cover_asset_id`（+ `cover_asset_id_space`）：模板封面；
- `template_pack_references.asset_id`（+ `asset_id_space`）：封装当前绑定的参考素材。

## 不改写什么

- `template_pack_versions.reference_ids`（+ `reference_ids_space`）：**已冻结的历史证据**。
  §21.4 明说绝不批量改写成 v2 id，否则历史任务不可复现；而且 `run.json` 里
  记的还是 v1 id，改了库里那一份两边就对不上。
- `replacement_run_inputs.asset_id`（+ `asset_id_space`）：历史运行输入，
  字节已由 `storage_key` 物化，回放不依赖 `assets` / `library_assets` 行。

这两处在下线 v1 之后由**只读 legacy 索引**兜底解析（见 §24.26）。

## 映射从哪来

`library_assets.source_key = 'legacy:<旧 id>'` 是 v1→v2 的唯一映射（由
`app library import-archive` 写入，`modules/library/import_archive.py` 的
`LEGACY_PREFIX` 定义）。不再按 sha256/名字猜：字节相同但归属不同是常见情况，
猜错会把封面的归属换给另一个人。

## 遇到缺映射就中止

有引用但查不到 v2 对应行 → 抛错并列出 id，不回退、不置空。缺映射说明
「这条素材还没追平」，正确动作是先跑 `app library import-archive` 再重跑迁移。
静默置空会让封装悄悄少一张参考图，而且要到用户生成失败时才发现。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d41c7a9e5f28"
down_revision: Union[str, Sequence[str], None] = "b7e3d9f2a418"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: `modules/library/import_archive.py` 的 `LEGACY_PREFIX`。
#: 迁移里刻意重写一遍而不是 import：迁移是历史快照，不能随业务常量漂移。
_LEGACY_PREFIX = "legacy:"

#: 参与改写的两处活引用：表 / id 列 / 空间列。
_REFERENCE_COLUMNS = (
    ("template_packs", "cover_asset_id", "cover_asset_id_space"),
    ("template_pack_references", "asset_id", "asset_id_space"),
)


def _legacy_mapping(bind: sa.engine.Connection) -> dict[str, str]:
    """`{旧 id: v2 id}`，来自 `source_key = 'legacy:<旧 id>'`。"""
    rows = bind.execute(
        sa.text(
            "SELECT id, source_key FROM library_assets WHERE source_key LIKE :prefix"
        ),
        {"prefix": f"{_LEGACY_PREFIX}%"},
    ).all()
    return {row.source_key[len(_LEGACY_PREFIX) :]: row.id for row in rows}


def _rewrite(
    bind: sa.engine.Connection,
    mapping: dict[str, str],
    table: str,
    id_column: str,
    space_column: str,
) -> None:
    """把 `table` 的活引用从 v1 id 换成 v2 id。

    缺映射时直接抛，并列出前 20 个 id —— 让人一眼知道该先跑什么命令，
    而不是看到一条「迁移失败」的堆栈。
    """
    rows = bind.execute(
        sa.text(
            f"SELECT id, {id_column} AS ref_id FROM {table} "  # noqa: S608 - 表名来自本文件常量
            f"WHERE {space_column} = 'v1' AND {id_column} IS NOT NULL"
        )
    ).all()
    missing = sorted({row.ref_id for row in rows if row.ref_id not in mapping})
    if missing:
        shown = ", ".join(missing[:20])
        raise RuntimeError(
            f"{table} 有 {len(missing)} 个引用找不到 v2 对应行：{shown}。"
            "先跑 `app library import-archive` 追平旧库，再重跑本迁移。"
        )
    for row in rows:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET {id_column} = :v2_id, {space_column} = 'v2' "  # noqa: S608
                "WHERE id = :row_id"
            ),
            {"v2_id": mapping[row.ref_id], "row_id": row.id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    mapping = _legacy_mapping(bind)
    for table, id_column, space_column in _REFERENCE_COLUMNS:
        _rewrite(bind, mapping, table, id_column, space_column)


def downgrade() -> None:
    """反向：v2 id 换回 v1 id。

    只对「v2 行是 legacy 导入来的」有效——手工上传到 v2 的素材没有 v1 前身，
    反向查不到就保持原样（那种行本来也不该被回滚成 v1 引用）。
    """
    bind = op.get_bind()
    inverse = {v2: v1 for v1, v2 in _legacy_mapping(bind).items()}
    for table, id_column, space_column in _REFERENCE_COLUMNS:
        rows = bind.execute(
            sa.text(
                f"SELECT id, {id_column} AS ref_id FROM {table} "  # noqa: S608
                f"WHERE {space_column} = 'v2' AND {id_column} IS NOT NULL"
            )
        ).all()
        for row in rows:
            legacy_id = inverse.get(row.ref_id)
            if legacy_id is None:
                continue
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET {id_column} = :v1_id, {space_column} = 'v1' "  # noqa: S608
                    "WHERE id = :row_id"
                ),
                {"v1_id": legacy_id, "row_id": row.id},
            )
