"""资产库 v2：草稿箱 / 个人 / 小组 / 团队 / 回收站五入口的统一资产库。

**与现役 `modules/asset` 并行的第二版素材库**，不是它的重构。并存期两边
完全隔离：表（`library_*`）、路由（`/library`）、权限（`library:*`）、
注入口（`library-lookup` / `library-ingest` / `library-references`）、
前端路径（`/library`）。删掉本模块不影响现役素材库。

M5 起入口已切到本模块（`/library` 是主入口）。**旧库不下线**：`/assets` 仍是可用的
旧版入口，观察期用 `app library reconcile` 与周期任务 `library.legacy.sync` 对账。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from core.ports.library_ingest import register_library_ingest
from core.ports.library_references import register_library_references
from modules.library.asset_references import GenerationAssetReferences
from modules.library.cli import register as register_cli
from modules.library.health import check as health_check
from modules.library.ingest import SqlLibraryIngest
from modules.library.lookup import install as install_lookup
from modules.library.references import SqlLibraryReferences
from modules.library.router import router

# 必须 import：注册 @periodic("library.trash.gc") / @periodic("library.download.gc") /
# @periodic("library.legacy.sync") 与 @register_handler（副作用导入）
from modules.library import downloads  # noqa: E402,F401  isort: skip
from modules.library import maintenance  # noqa: E402,F401  isort: skip
from modules.library import observation  # noqa: E402,F401  isort: skip

# 把「v2 资产是什么」挂到注入口：模板 / 任务 / 选择器在并存期要读它，
# 但它们不能 import 本模块（业务模块之间不许互相 import）。
install_lookup()

# 生成结果写进草稿箱的唯一通道。fail-closed —— 没接上时生成侧 503，
# 而不是「生成成功但库里找不到」。
register_library_ingest(SqlLibraryIngest())

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
# - M2：`update` / `delete` / `delete:any` / `draft:review` / `share` /
#   `share:direct` / `share:approve` / `tag:manage`；
#   2026-11-10 追加：`share:approve:team` / `share:approve:group`。
# - M3（本批）：`generation:ingest` —— 生成入库接口用 `require` 卡住。
#   它与 `create` 分开：能上传的人不应该能伪造「这是某次生成的结果」。
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
        PermissionSpec(
            "library:share:approve",
            "全站审批共享与撤销申请（兼容码，等同同时勾选下面两项）",
            group="资产库",
        ),
        PermissionSpec(
            "library:share:approve:team",
            "审批团队级共享与撤销申请",
            group="资产库",
        ),
        PermissionSpec(
            "library:share:approve:group",
            "审批所在小组的共享与撤销申请",
            group="资产库",
        ),
        PermissionSpec("library:tag:manage", "管理标签词表（改名 / 删除）", group="资产库"),
        PermissionSpec(
            "library:generation:ingest", "生成结果入库（草稿箱）", group="资产库"
        ),
    ),
    # 声明了就必须注册，`create_app()` 会核对：上面三条 import 就是注册动作。
    # `library.legacy.sync` 是并存观察期的每日对账（只读、只记日志）；
    # 观察期结束后随旧模块一起删掉，它不再是 v2 的部署条件。
    jobs=("library.download.build",),
    periodic=("library.trash.gc", "library.download.gc", "library.legacy.sync"),
    # 运维命令：旧素材导入、回收站 GC、打包产物清理
    cli=register_cli,
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
    doctor=health_check,
)
