"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

## 为什么改

把「为什么加这个约束、旧数据怎么办、能不能回滚」写在这里。
`alembic history` 是给三个月后的人看的，别只留一句 auto-generated。
有数据的表上加减非空列时，先在 sqlite 上跑一遍 `upgrade` 再提交
（sqlite 缺 ALTER TABLE，`render_as_batch` 会重建整张表）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
