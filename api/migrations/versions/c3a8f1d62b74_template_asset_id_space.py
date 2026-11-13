"""模板库素材 id 加「空间标记」列（v1 `assets` / v2 `library_assets`）。

v1 与 v2 的主键都是 32 位 uuid hex（`db/base.py::UUIDMixin`），**形状上不可区分**。
读路径切到 v2 之后，`template_pack_references.asset_id` 这类列会同时装着两套 id：
历史行指向 v1，新行指向 v2。没有标记就只能靠「查不到再回退另一侧」这种隐式兜底，
而 `core/ports/library_lookup.py:8-10` 明确禁止那么做——兜底会让某一侧悄悄读到
另一半数据，且只在删除之后才暴露成裂图。

所以四列都按同一形状加：先 `nullable=True` → 回填 `v1` → 再改 `nullable=False`。
**不批量改写 id 本身**：`template_pack_versions.reference_ids` 是已冻结的历史证据，
改写它会让历史任务不可复现（`asset_references.py:6-8` 明说历史版本不算引用）。

downgrade 直接丢列：标记是可以重建的（回填 `v1` 就是它唯一的初始值），
而把 `v2` 行改回 `v1` 会让它们指向不存在的旧素材——丢掉比写坏安全。

review 时注意：`batch_alter_table` 在 SQLite 上会重建表，`server_default` 不跟着
迁移到模型（模型侧用的是 Python 默认值 `asset_id_space_default()`），
`tests/test_migrations.py::test_models_match_migrations` 就是这道对账。
"""

import sqlalchemy as sa
from alembic import op

revision = "c3a8f1d62b74"
down_revision = "b7c4e2a18d63"
branch_labels = None
depends_on = None

#: (表名, 新列名, 说明)。四处都是「一列 asset id + 一个空间标记」的同一形状。
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("template_packs", "cover_asset_id_space"),
    ("template_pack_references", "asset_id_space"),
    ("template_pack_versions", "reference_ids_space"),
    ("replacement_run_inputs", "asset_id_space"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column(column, sa.String(length=4), nullable=True))
        # 建表以来所有行都是 v1 的 id：这一刻的真相就是 `v1`，不是「未知」。
        op.execute(f"UPDATE {table} SET {column} = 'v1' WHERE {column} IS NULL")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=4),
                nullable=False,
            )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column(column)
