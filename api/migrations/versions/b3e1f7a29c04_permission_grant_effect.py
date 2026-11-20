"""个人例外：`permission_grants.effect`

给「只对这个人关掉某一项」一个表达方式。以前减法只能做到**共享单元**：
撤角色会连累同角色的人，把人移出角色会丢掉该角色的全部权限，
所以「撤掉张三的 task:delete」实际要动两三处、还要给别人补回去。

## 只加一列，不加一张表

判定每请求都要一次读出「给的和不给的」，分两张表就是两次往返；而
「整组替换」「码是否合法」「删组清绑定」这些规则对两者完全一样。
`deny` 只允许出现在个人层（`scope='user'`），这条由 service 的唯一入口守。

## 唯一约束要从三列改成四列

原来的 `(scope, scope_id, permission)` 会让「同一个人对同一个码既有一条
allow、又有一条 deny」直接冲突 —— 而这正是「先否决、后来又重新授权」的
合法路径。所以约束扩成四列。

## 可逆

降级能正常走完：先删掉 deny 行（否则它们会变成与 allow 同键的重复行，
卡在恢复出来的三列约束上），再恢复三列唯一、最后删列。**不标**
`irreversible` —— `downgrade()` 不抛。代价是降级会丢掉所有例外记录，
这一点写在 PR 说明里。
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3e1f7a29c04"
down_revision: Union[str, None] = "c4f7a2b81d63"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_TABLE = "permission_grants"
_UNIQUE = "uq_permission_grants_scope_code"


def upgrade() -> None:
    # batch：sqlite 没有 ALTER TABLE，只能重建整张表（本仓迁移的既有约定）
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "effect",
                sa.String(length=8),
                nullable=False,
                server_default="allow",
            )
        )
        batch_op.drop_constraint(_UNIQUE, type_="unique")
        batch_op.create_unique_constraint(
            _UNIQUE, ["scope", "scope_id", "permission", "effect"]
        )


def downgrade() -> None:
    # 先清 deny 行：删列之后它们会退化成与 allow 同键的重复行，
    # 恢复三列唯一约束时直接失败。
    op.execute(f"DELETE FROM {_TABLE} WHERE effect = 'deny'")
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_constraint(_UNIQUE, type_="unique")
        batch_op.create_unique_constraint(
            _UNIQUE, ["scope", "scope_id", "permission"]
        )
        batch_op.drop_column("effect")
