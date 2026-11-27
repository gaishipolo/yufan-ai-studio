"""下线旧的「带筛选约束的封装模板」两张表

Revision ID: c6f0a2d7e813
Revises: a3c7e91b2d45
Create Date: 2026-11-14 09:55:00.000000

## 为什么删

用户拍板「按 `template_packager` 的能力完整迁移，原有的 `template_pack` 功能可以
覆盖」。C1 已经把配置层（`template_manifests` 四表）与 `/template-manifests`
接口做完；C2 又把真正的素材包 / 视频模板（`template_packs` 三表 +
`video_templates`）从 `modules/template` 整体搬进本模块。两套能力都在之后，
`template_pack_templates` / `template_pack_slots` 不再有任何读者：

- 服务、路由、权限点（`template_pack:*` 里只留 manifest 那三个）已同批删除；
- `/template-packs` 的六个端点下线，前端对应页面在收尾阶段一并移除。

留着一张没人读的表比删掉更糟：下一个人会以为它是活的，迁移时还会被
`compare_metadata` 逼着维护。

## 不可逆

表结构能重建，**数据不能**。让 `downgrade` 重建空表再「成功」返回，等于静默
丢数据；所以这条迁移标了 `irreversible = True`，且 `downgrade()` 直接抛。
真要不丢数据，先备份再升级。
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6f0a2d7e813"
down_revision: Union[str, Sequence[str], None] = "a3c7e91b2d45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 本迁移不可逆：表结构可重建，被删的数据回不来（见模块 docstring 的「不可逆」一节）。
#: 机器可读判据：`scripts/downgrade_anchor.py` 靠它算 CI 的降级锚点。
irreversible = True


def upgrade() -> None:
    # 先删槽位（它带指向主表的外键），再删主表。
    op.drop_table("template_pack_slots")
    op.drop_table("template_pack_templates")


def downgrade() -> None:
    # 不可逆：表结构可重建，被删的数据回不来。
    raise NotImplementedError("旧封装模板已下线，删掉的行没有备份，迁移不可回退")
