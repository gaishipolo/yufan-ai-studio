"""新增 `video_tasks.params_json`：存模型声明的其余参数

Revision ID: e6a1b3c8d045
Revises: d5f8a2c7b914
Create Date: 2026-11-16 11:20:00.000000

## 为什么改

时长与画幅有顶层列，计费模式与风格预设也各有一列，但模型随时可能再声明
别的参数（`quality` / `seed` / `resolution`…）。没有这一列时前端只有两个
选择：写死一套键（模型一改就 400），或者把声明的参数全塞进提示词
（供应商收到的是一段文字，不是参数）。

于是工作台曾把「时长 / 画幅 / 风格预设」三件套写死在表单里，其中
`stylePreset` **所有模型都没声明** —— 提交必定 400（实测：
`模型 ... 没有声明风格预设（stylePreset），不能指定风格`）。

## 迁移什么

加一列 `params_json`（JSON 文本，空参数写空串），与生图
`image_jobs.params_json` 同一口径：

- 受理期按模型 `paramSchema` 校验，**未声明的键 400**（打错字被静默忽略
  的话，用户改的那个值从来没生效过，页面看起来却一切正常）；
- 顶层那几个键（`durationSec` / `aspectRatio` / `stylePreset` /
  `billingMode`）**不进这里** —— 避免两份真相（顶层改了、这里没改）。

## 降级

直接删列即可，存量数据不丢（历史行本来就没有这一列的内容）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a1b3c8d045"
down_revision: str | Sequence[str] | None = "d5f8a2c7b914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        # 表里已经有行，NOT NULL 必须带 server_default 才加得上去。
        batch_op.add_column(
            sa.Column(
                "params_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
    # server_default 留在库里会让「新增行漏写这个键」也悄悄成功。
    # 约束要回到应用层：模型 ORM 有 default，新建行照样能拿到空串。
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.alter_column("params_json", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("video_tasks", schema=None) as batch_op:
        batch_op.drop_column("params_json")
