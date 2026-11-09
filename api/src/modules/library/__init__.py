"""资产库 v2：草稿箱 / 个人 / 小组 / 团队 / 回收站五入口的统一资产库。

**与现役 `modules/asset` 并行的第二版素材库**，不是它的重构。并存期两边
完全隔离：表（`library_*`）、路由（`/library`）、权限（`library:*`）、
注入口（`library-lookup` / `library-ingest` / `library-references`）、
前端路径（`/library`）。切换时只改前端模块的 `load()` 一行，删掉本模块
不影响现役素材库。

M2 交付：三层可见集、共享与撤销审批、删除矩阵与 §3.4.1 窄口、草稿与回收站
状态机、标签、批量写、选择器。封装与批量下载在 M3。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from core.ports.library_references import register_library_references
from modules.library.asset_references import GenerationAssetReferences
from modules.library.cli import register as register_cli
from modules.library.health import check as health_check
from modules.library.lookup import install as install_lookup
from modules.library.references import SqlLibraryReferences
from modules.library.router import router

# 必须 import：注册 @periodic("library.trash.gc")（副作用导入）
from modules.library import maintenance  # noqa: E402,F401  isort: skip

# 把「v2 资产是什么」挂到注入口：模板 / 任务 / 选择器在并存期要读它，
# 但它们不能 import 本模块（业务模块之间不许互相 import）。
install_lookup()

# 彻底删除前的引用检查：v2 自己的封装成员与下载任务在这里聚合。
# fail-closed —— 没接上时 `purge` 一律 503，不把「查不到」当「没人引用」。
register_library_references(SqlLibraryReferences())

# v2 绝大多数 `asset_id` 列指向 `library_assets.id`，与旧 `assets.id` 无关；
# 唯一例外是生成溯源里记录的旧素材参考图——删掉旧图会让历史详情页留白。
ASSET_REFERENCE_SOURCES = (
    AssetReferenceSource(
        name="library_asset_generation.reference_asset_ids",
        checker=GenerationAssetReferences(),
        location="modules/library/models.py:LibraryAssetGeneration.reference_asset_ids",
        description="生成溯源快照里记录的旧素材库参考图",
    ),
)
bind_asset_reference_sources("library", ASSET_REFERENCE_SOURCES)

# 权限点**随实现分批进入 ModuleSpec**，不是一次性声明 11 个。
#
# `tests/test_permission_coverage.py` 的红线：声明了却没有任何 `require(...)` /
# `actor.has(...)` 判定点的权限码，会在角色配置界面变成一个永远勾了没用的项。
# 所以：
#
# - M1：`read` / `create`；
# - M2（本批）：`update` / `delete` / `delete:any` / `draft:review` / `share` /
#   `share:direct` / `share:approve` / `tag:manage` —— 全部已接线：
#   `update`/`delete`/`share`/`tag:manage` 在路由 `require`，
#   `delete:any`/`draft:review`/`share:direct` 在服务层 `actor.has`；
# - M3：`generation:ingest`（生成入库接口接线时）。
module = ModuleSpec(
    name="library",
    router=router,
    tags=["资产库"],
    description="资产库 v2：草稿 / 个人 / 小组 / 团队 / 回收站",
    permissions=(
        PermissionSpec("library:read", "查看资产库", group="资产库"),
        PermissionSpec("library:create", "上传与封装资产", group="资产库"),
        PermissionSpec("library:update", "修改本人资产的名称与标签", group="资产库"),
        PermissionSpec("library:delete", "删除本人资产（移入回收站）", group="资产库"),
        PermissionSpec("library:delete:any", "删除 / 恢复任何人的资产", group="资产库"),
        PermissionSpec("library:draft:review", "审核他人草稿", group="资产库"),
        PermissionSpec("library:share", "发起共享与撤销共享申请", group="资产库"),
        PermissionSpec("library:share:direct", "共享无需审批（撤销仍需审批）", group="资产库"),
        PermissionSpec("library:share:approve", "审批共享与撤销申请", group="资产库"),
        PermissionSpec("library:tag:manage", "管理标签词表（改名 / 删除）", group="资产库"),
    ),
    # M2 起有周期任务（回收站 GC）。声明了就必须注册，`create_app()` 会核对：
    # `modules.library.maintenance` 的 import 就是注册动作。
    periodic=("library.trash.gc",),
    # 运维命令：旧素材导入、回收站 GC（dry-run / 手动跑一轮）
    cli=register_cli,
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
    doctor=health_check,
)
