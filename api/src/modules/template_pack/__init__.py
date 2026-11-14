"""模板封装模块：模板规格（槽位 + 提示词 + 模型）。

本文件是模块对外的唯一契约：路由 + 权限点 + 素材引用声明。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。

## 职责边界（用户拍板）

**模板封装只负责把「要什么素材 + 提示词 + 用哪个模型」定下来。** 生成是
`modules/task` 的消费行为，不在本模块实现、也不由本模块去接：本模块
**不取字节、不查素材元数据、不物化**，素材校验一律经 `core.ports.library_lookup`。

## 2026-11-14 P3：只剩模板规格

素材包（`template_packs` 四表）、视频模板（`video_templates`）、manifest 配置
（`template_manifests` 四表）与替换调用档案（`replacement_runs` 三表）连同
`modules/template` 整条替换执行链一起下线 —— 项目里只剩两个概念：
**模板（本模块）与任务（task）**。多图语义由 `source=pack` 槽位指向资产库封装
（`kind=pack`）表达，模板不复制成员清单。

两个注入口（`template_catalog` / `template_runtime`）随旧表一起删除：
它们存在的理由是「编写侧 manifest 与运行侧投影是两套表」，现在发布快照
（`template_spec_versions` + `_slots`）本身就是运行侧唯一事实，中间层没有意义。

## 权限点为什么没有 `:delete`

模板是资产：删了会让引用它的编号失效。所以只有「停用」（保留历史、不再对外），
没有「删除」。留着 `:delete` 会在角色配置界面多一个勾了没用的项
（`test_permission_coverage.py` 的红线：声明了却没有任何判定点的权限码会红）。
"""

from fastapi import APIRouter

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from core.ports.library_references import (
    LibraryReferenceSource,
    bind_library_reference_sources,
)
from modules.template_pack.library_references import SpecFixedAssets
from modules.template_pack.spec_router import router as spec_router

router = APIRouter()
router.include_router(spec_router)

# 资产「彻底删除」前问「还有谁在用」。已发布版本里 `binding=fixed` 的槽位
# 直接咬着 asset id，删了这张图 = 该版本再也编不出原文（编号对不上素材）。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="template_spec_slots.asset_id",
        checker=SpecFixedAssets(),
        location="modules/template_pack/models.py:TemplateSpecSlot.asset_id",
        description="已发布版本里固定槽位绑定的 v2 资产；草稿不在此表，不拦",
    ),
)
bind_library_reference_sources("template_pack", LIBRARY_REFERENCE_SOURCES)

module = ModuleSpec(
    name="template_pack",
    router=router,
    tags=["模板封装"],
    description="模板封装：模板规格（槽位 / 提示词 / 模型），发布后供模板库填表",
    permissions=(
        PermissionSpec("template_pack:read", "查看模板封装", group="内容生产"),
        PermissionSpec("template_pack:create", "创建或保存模板封装草稿", group="内容生产"),
        PermissionSpec("template_pack:publish", "发布或停用模板封装", group="内容生产"),
    ),
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
)
