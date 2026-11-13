"""资产库 v2：草稿箱 / 个人 / 小组 / 团队 / 回收站五入口的统一资产库。

2026-11-13 起它是**唯一的**资产库：旧素材库（`modules/asset` + `/assets` + `assets`
表）已彻底下线（迁移 `f5b2d8c4a173`），两套 id 空间的并存期结束。
历史任务里的 v1 id 不再解析得到素材名，详情页按「素材已删除」降级。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from core.ports.library_ingest import register_library_ingest
from core.ports.library_references import (
    LibraryReferenceSource,
    bind_library_reference_sources,
)
from modules.library.cli import register as register_cli
from modules.library.health import check as health_check
from modules.library.ingest import SqlLibraryIngest
from modules.library.lookup import install as install_lookup
from modules.library.references import DownloadJobReferences, PackMemberReferences
from modules.library.router import router

# 必须 import：注册 @periodic("library.trash.gc") / @periodic("library.download.gc")
# 与 @register_handler（副作用导入）
from modules.library import downloads  # noqa: E402,F401  isort: skip
from modules.library import maintenance  # noqa: E402,F401  isort: skip

# 把「v2 资产是什么」挂到注入口：模板 / 任务 / 选择器在并存期要读它，
# 但它们不能 import 本模块（业务模块之间不许互相 import）。
install_lookup()

# 生成结果写进草稿箱的唯一通道。fail-closed —— 没接上时生成侧 503，
# 而不是「生成成功但库里找不到」。
register_library_ingest(SqlLibraryIngest())

# 彻底删除前的引用检查：v2 自己的封装成员与下载任务在这里聚合。
# fail-closed —— 没接上时 `purge` 一律 503，不把「查不到」当「没人引用」。
#
# **一个来源一条声明**，`location` 指向真实写入点：`tests/test_architecture.py`
# 的 `test_library_reference_sources_are_complete_after_discovery` 拿它做完备性对账，
# 而 `test_every_library_reference_writer_declares_a_source` 反过来扫源码里的
# `reference_library_ids` / `library_pack_id` 一类字段——新增一处写入却忘了在这里
# 声明，CI 直接红，不必等用户发现「删掉的图在封装里裂了」。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="library_pack_items.asset_id",
        checker=PackMemberReferences(),
        location="modules/library/models.py:LibraryPackItem.asset_id",
        description="v2 封装的成员；已解散（在回收站）的封装不算",
    ),
    LibraryReferenceSource(
        name="library_download_jobs.asset_ids",
        checker=DownloadJobReferences(),
        location="modules/library/models.py:LibraryDownloadJob.asset_ids",
        description="已经打进下载包的任务（仅 queued / running / succeeded）",
    ),
)
bind_library_reference_sources("library", LIBRARY_REFERENCE_SOURCES)

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
    jobs=("library.download.build",),
    periodic=("library.trash.gc", "library.download.gc"),
    # 运维命令：回收站 GC、打包产物清理
    cli=register_cli,
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
    doctor=health_check,
)
