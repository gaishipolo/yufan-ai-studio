"""下线替换链与旧封装表：模板只存在于 `template_specs`

Revision ID: b8e4c1f90d27
Revises: d4a9f1c73b20
Create Date: 2026-11-14 15:20:00.000000

## 为什么一次删 11 张表

2026-11-14 拍板「统一到 task」：替换不再是平行链路，而是「某个声明了替换能力的
模型 + 某组槽位」的普通生成，落的就是 `tasks`。于是三组表同时失去读者：

| 表 | 谁在读 | 取代物 |
|---|---|---|
| `video_templates` | 替换执行链 `find_template_for_run()` | `template_spec_versions` + `_slots` |
| `template_packs` / `_references` / `_versions` | `template_runtime` 端口 → 执行链 | 资产库封装（`kind=pack`）+ `source=pack` 槽位 |
| `template_manifests` 四表 | 编写侧目录端口 → 封装页 | 同上：一条 function = 一条 spec |
| `replacement_runs` / `_run_inputs` / `_reviews` | 替换工作台、任务中心第三源 | 任务中心只读 `tasks` + `image_jobs` |

`modules/template` 整条执行链（编排 / 物化 / 归档工作区 / 发布回执 / 运维命令）
随表一起删除，`core.feed`、`core.ports.template_runtime`、
`core.ports.template_catalog`、`core/template_pack_view` 四个跨模块契约也一并
下线 —— 它们存在的理由都是「编写侧与运行侧是两套表」，现在发布快照本身就是
运行侧唯一事实。

## 历史调用档案为什么直接删

`replacement_runs` 曾被当作「重跑与复核的唯一依据」，所以上一版计划是「先关写
入口、表保留只读一个周期」。**用户拍板直接删**，理由是这批行记录的是旧链路的
中间态（`prompt_sha256`、归档工作区指针、渠道快照），形状与 `tasks` 不对应，
迁不成新记录；留一份只读视图反而让人以为旧链路还活着。开发库 49 行历史记录
随之消失，这是明确接受的代价。

## 能不能回滚

**不能**。数据随能力一起下线，重建空表只会把「降级成功」变成一句谎话。
要回到旧结构，从 `cb9122d`（本迁移之前）取模型与路由，按需另写迁移；
真的要保数据就只能靠升级前的库备份。
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4c1f90d27"
down_revision: Union[str, Sequence[str], None] = "d4a9f1c73b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 先删子表再删主表：项目里没有真外键，但顺序仍按依赖排，避免将来加了外键
#: 之后这条迁移变成「只在旧库上能跑的那一条」。
_DROP_ORDER: tuple[str, ...] = (
    "template_manifest_references",
    "template_manifest_functions",
    "template_manifest_packs",
    "template_manifests",
    "template_pack_references",
    "template_pack_versions",
    "template_packs",
    "video_templates",
    "replacement_reviews",
    "replacement_run_inputs",
    "replacement_runs",
)


def upgrade() -> None:
    for table in _DROP_ORDER:
        # 索引与唯一约束由 drop_table 一并带走，不需要逐个 drop_index。
        op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError(
        "这条迁移不可降级：11 张表的数据随替换链一起下线，重建空表只会让"
        "「降级成功」变成谎话。要恢复旧结构，请从 cb9122d 取模型与路由另写迁移；"
        "要恢复数据只能靠升级前的库备份。"
    )
