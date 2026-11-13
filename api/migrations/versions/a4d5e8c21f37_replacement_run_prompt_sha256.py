"""`replacement_runs` 加 `prompt_sha256`：拿它复核「当时到底发了什么」。

编译器交出来的两个哈希都不是最终 prompt 的摘要：`template_sha256` 哈希模板、
`spec_sha256` 哈希输入，而之后 Python 还会 `inject_masked_text_refs` 注入文本正文、
（bailian 渠道）`translate_prompt` 改写引用写法。结果是一条已付费任务的档案里
**没有任何一个值能对上实际发出的那段字节**。

所以这一列存「所有后处理之后」的 sha256，与 `records/prompt-compilation.json`
里的同名字段、`inputs/prompt.txt` 三处都是同一份字符串。

**不回填**：老行留 `NULL`。回填只能哈希现在这一列 `prompt`——如果当年发出去的
和存下来的不一致（这正是这列要抓的情况），回填会把不一致变成「看起来一致」。

downgrade 直接丢列（可重建，只是老行仍然没有）。
"""

import sqlalchemy as sa
from alembic import op

revision = "a4d5e8c21f37"
down_revision = "f2d7a91c4b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("replacement_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("prompt_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("replacement_runs", schema=None) as batch_op:
        batch_op.drop_column("prompt_sha256")
