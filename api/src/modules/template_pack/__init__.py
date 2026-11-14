"""模板封装模块：素材包 + 视频模板 + manifest 配置。

本文件是模块对外的唯一契约：路由 + 权限点 + 注入口 + 种子 + 运维命令。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。

## 职责边界（用户拍板）

**模板封装模块只负责把封装数据管好、通过 ports 对外提供。** 生成是
`modules/task` 的消费行为，不在本模块实现、也不由本模块去接。

两个注入口，读的是两套数据，别合并：

- `core.ports.template_catalog`：**编写侧**只读目录（manifest / packs / functions）。
- `core.ports.template_runtime`：**运行侧**投影（`template_packs` + `_versions` +
  `video_templates`），替换执行链只认这一个。

## 2026-11-14 C2：素材包与视频模板整体并入

之前这两套表在 `modules/template` 里，与替换执行链混住。用户拍板「封装模块只管
数据、通过 ports 对外提供」，于是整块搬到这里：

- 表：`template_packs` / `template_pack_references` / `template_pack_versions` /
  `video_templates`（表名不变，无需数据迁移）；
- 路由：`/templates/replacement-packs`、`/templates/video-templates`（路径不变）；
- 权限码：`template:pack:manage` / `template:pack:publish`（码不变，已有角色无需重授权）。

同一次搬迁把旧的「带筛选约束的封装模板」（`template_pack_templates` /
`template_pack_slots`）下线了——用户明确「原有 template_pack 功能可以覆盖」。

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
from core.ports.template_runtime import register_template_runtime
from modules.template_pack.cli import register as register_cli
from modules.template_pack.library_references import PackLibraryReferences
from modules.template_pack.manifest_router import router as manifest_router
from modules.template_pack.router import router as templates_router
from modules.template_pack.runtime_port_impl import TemplateRuntime
from modules.template_pack.seed import seed as seed_demo
from modules.template_pack.spec_router import router as spec_router

router = APIRouter()
router.include_router(templates_router)
router.include_router(manifest_router)
router.include_router(spec_router)

# 资产「彻底删除」前问「还有谁在用」。素材包 head 里的引用由本模块声明；
# 不拦的四处（冻结版本 / 运行输入 / 视频模板清单 / 封面）在
# `library_references.py` 的表格里逐条写了理由。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="template_pack_references.library_asset_id",
        checker=PackLibraryReferences(),
        location="modules/template_pack/models.py:TemplatePackReference.asset_id",
        description="封装 head 里引用的 v2 资产；已删除的封装与冻结的历史版本不算",
    ),
)
bind_library_reference_sources("template_pack", LIBRARY_REFERENCE_SOURCES)

module = ModuleSpec(
    name="template_pack",
    router=router,
    tags=["模板封装"],
    description="模板封装：素材包 / 视频模板 / manifest 配置，对外经 ports 提供已发布数据",
    permissions=(
        # manifest 配置接口（C1 新增）
        PermissionSpec("template_pack:read", "查看模板封装", group="内容生产"),
        PermissionSpec("template_pack:create", "创建或保存模板封装草稿", group="内容生产"),
        PermissionSpec("template_pack:publish", "发布或停用模板封装", group="内容生产"),
    ),
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
    # 演示数据：一个已发布的人物包 + 一个产品包草稿。顺序 30 依赖 identity（10）
    # 已经建好演示账号。
    seed=seed_demo,
    seed_order=30,
    # 运维命令：import-template-packs / import-video-templates。
    # 调用档案那半在 modules/template（`import-template-runs`），一张表归一个模块。
    cli=register_cli,
)

# 两个注入口的注册都是 import 期副作用（不 import 就不可能有实现）：
# catalog 是编写侧只读目录，runtime 是替换链要的运行投影。
from modules.template_pack import catalog_port_impl  # noqa: E402,F401  isort: skip

catalog_port_impl.install()
register_template_runtime(TemplateRuntime())
