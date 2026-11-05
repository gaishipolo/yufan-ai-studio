"""identity: users.password_changed_at

改密码后让旧令牌失效（R4-13）。令牌是无状态的，唯一能做的是「签发时间 vs 改密时间」
比一下：`iat < password_changed_at` 的令牌一律拒绝（见 `identity/principal.py`）。

列可空，语义是「从未改过密码」——存量账号保持 NULL，升级不会把人踢下线；
真正改过一次口令之后，才把此前的令牌全部作废。

Revision ID: 9c1f4e2a7b83
Revises: 2e770625c6a3
Create Date: 2026-11-05 15:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1f4e2a7b83"
down_revision: str | Sequence[str] | None = "2e770625c6a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
