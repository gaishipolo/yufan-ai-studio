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
**模板（本模块）与任务（task）**。多图语义由一个可多值的图片/视频槽位表达
（`min/max`），模板不复制素材清单。

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
from core.pages import PageSpec
from core.permissions import PermissionSpec
from core.ports.library_references import (
    LibraryReferenceSource,
    bind_library_reference_sources,
)
from modules.template_pack.case_router import router as case_router
from modules.template_pack.library_references import (
    CaseSnapshots,
    SpecDemoCases,
    SpecFixedAssets,
)
from modules.template_pack.spec_router import router as spec_router

router = APIRouter()
router.include_router(spec_router)
router.include_router(case_router)

# 资产「彻底删除」前问「还有谁在用」。已发布版本里 `binding=fixed` 的槽位
# 直接咬着 asset id，删了这张图 = 该版本再也编不出原文（编号对不上素材）。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="template_spec_slots.asset_id",
        checker=SpecFixedAssets(),
        location="modules/template_pack/models.py:TemplateSpecSlot.asset_id",
        description="已发布版本里固定槽位绑定的 v2 资产；草稿不在此表，不拦",
    ),
    # 展示案例：卡片上的封面与悬停视频就是这条素材本身，删了卡片会静默变空白。
    LibraryReferenceSource(
        name="template_specs.draft.demoAssetId",
        checker=SpecDemoCases(),
        location="modules/template_pack/spec_service.py:publish（draft.demoAssetId）",
        description="模板草稿里指向的展示案例；软删模板恢复后仍算引用",
    ),
    # 案例快照：模板案例里存着配置副本，固定槽位照样咬着那张图。
    LibraryReferenceSource(
        name="template_cases.config",
        checker=CaseSnapshots(),
        location="modules/template_pack/models.py:TemplateCase.config",
        description="模板案例快照里固定槽位与展示案例指向的 v2 资产；下架/软删后仍算引用",
    ),
)
bind_library_reference_sources("template_pack", LIBRARY_REFERENCE_SOURCES)

module = ModuleSpec(
    name="template_pack",
    router=router,
    tags=["模板封装"],
    description="模板封装：模板规格（槽位 / 提示词 / 模型），发布后供模板库填表",
    permissions=(
        PermissionSpec(
            "template_pack:read",
            "查看模板封装",
            group="内容生产",
            pages=("page:template-specs", "page:templates"),
        ),
        PermissionSpec(
            "template_pack:create",
            "创建或保存模板封装草稿",
            group="内容生产",
            pages=("page:template-specs",),
        ),
        PermissionSpec(
            "template_pack:publish",
            "发布或停用模板封装",
            group="内容生产",
            pages=("page:template-specs",),
        ),
        PermissionSpec(
            "template_pack:case:read",
            "查看模板案例库并复用案例",
            group="内容生产",
            pages=("page:template-specs",),
        ),
        PermissionSpec(
            "template_pack:case:write",
            "发布或管理模板案例（编辑 / 下架 / 删除）",
            group="内容生产",
            pages=("page:template-specs",),
        ),
    ),
    # 两页共用 `template_pack:read`：模板库是「看 + 填表」，封装页是「做模板」。
    # 分开声明是为了「先只让人用模板、不让人改模板」这种最常见的授权诉求，
    # 而共享的读权限靠交集门控保住（关掉封装页不会把模板库一起打残）。
    pages=(
        PageSpec(
            code="page:templates",
            name="模板库",
            route="/templates/library",
        ),
        PageSpec(
            code="page:template-specs",
            name="模板封装",
            route="/templates/specs",
        ),
    ),
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
)
