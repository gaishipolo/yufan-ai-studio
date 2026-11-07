"""queued_jobs 加领取凭据

Revision ID: b3d6f8a1c204
Revises: a1c4e7f2b930
Create Date: 2026-11-08 00:40:00

## 为什么加这一列

在此之前，「领取」只有 `lease_expires_at` 一个标记：到期就能被别人接手。
问题出在**旧主人**这一侧——它并不知道自己已经被接手了，仍然拿着同一个 job id
继续跑，最后照常 `ack`。`ack` 是 `DELETE ... WHERE id=?`，于是新主人正在跑的那一行
被删掉了：新主人一旦再崩一次，这条 job 就凭空消失（连死信都留不下）。

`lease_token` 让每次领取都有凭据：续租、`ack`、`nack` 都要求 token 对得上，
对不上就是「这行不归你了」，一律不动。旧主人因此只能白跑，不能造成破坏。

列可为空：历史行（升级时正在跑的 job）没有凭据，它们的回执会退化成
「只动 `status='running'` 的行」——比原来的无条件删除安全，也不会卡住升级。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d6f8a1c204"
down_revision: str | Sequence[str] | None = "a1c4e7f2b930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 可空、无默认值：sqlite 的 batch 重建只带原有列，新列取 NULL 即可，
    # 不会像「NOT NULL + 去默认值」那样在有数据的库上炸（见 a1c4e7f2b930 的注释）。
    with op.batch_alter_table("queued_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lease_token", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("queued_jobs", schema=None) as batch_op:
        batch_op.drop_column("lease_token")
