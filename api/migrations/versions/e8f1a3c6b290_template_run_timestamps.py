"""template: `replacement_runs` 补 `started_at` / `finished_at`

## 为什么改
这两列在老开发库里存在（早期初始 schema 留下的），但当前迁移链重建
`replacement_runs`（`df546efef239`）时没有带上它们，模型也一直没声明。
于是「全新库没有、老库有」，`alembic check` 只在老库报差异，而测试从空库
upgrade 时又看不见它们——同一个 schema 有两种事实。

## 旧数据怎么办
不删。两列是耗时统计，历史值有保留价值；这里只给**缺列的环境**补上，
已有列的环境跳过（`sa.inspect` 判断），不会覆盖数据。

## 能否回滚
能。`downgrade()` 只在列存在时删除；历史耗时数据会丢（这两列无人写读，
删掉不影响任何业务流程）。

Revision ID: e8f1a3c6b290
Revises: c7d9e1f3a580
Create Date: 2026-11-09 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f1a3c6b290"
down_revision: str | Sequence[str] | None = "c7d9e1f3a580"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("started_at", "finished_at")


def _existing_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("replacement_runs")}


def upgrade() -> None:
    existing = _existing_columns()
    missing = [name for name in _COLUMNS if name not in existing]
    if not missing:
        return
    with op.batch_alter_table("replacement_runs") as batch:
        for name in missing:
            batch.add_column(sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    existing = _existing_columns()
    present = [name for name in _COLUMNS if name in existing]
    if not present:
        return
    with op.batch_alter_table("replacement_runs") as batch:
        for name in present:
            batch.drop_column(name)
