"""资产库 v2：草稿箱 / 个人 / 小组 / 团队 / 回收站五入口的统一资产库。

**与现役 `modules/asset` 并行的第二版素材库**，不是它的重构。并存期两边
完全隔离：表（`library_*`）、路由（`/library`）、权限（`library:*`）、
注入口（`library-lookup` / `library-ingest` / `library-references`）、
前端路径（`/library`）。切换时只改前端模块的 `load()` 一行，删掉本模块
不影响现役素材库。

M1 交付「个人库」竖线：10 张表、上传 / 秒传 / `sourceKey` 幂等、列表与取件、
旧素材导入 CLI。可见性、共享、草稿与回收站在 M2。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from modules.library.asset_references import GenerationAssetReferences
from modules.library.cli import register as register_cli
from modules.library.health import check as health_check
from modules.library.lookup import install as install_lookup
from modules.library.router import router

# 把「v2 资产是什么」挂到注入口：模板 / 任务 / 选择器在并存期要读它，
# 但它们不能 import 本模块（业务模块之间不许互相 import）。
install_lookup()

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
# M0 曾一次性声明 11 个，全量测试立刻报 9 个僵尸码——那不是在提醒「还没写」，
# 而是在说「现在就已经是装饰品」。所以：
#
# - M1（本批）：`read` / `create` —— 列表、上传、源视频上传、取件都已接线；
# - M2 加：`update` / `delete` / `delete:any` / `draft:review` / `share` /
#   `share:direct` / `share:approve` / `tag:manage`；
# - M3 加：`generation:ingest`（生成入库接口接线时）。
#
# 设计文档 §5 的清单仍是目标形态，只是分批判落地。
module = ModuleSpec(
    name="library",
    router=router,
    tags=["资产库"],
    description="资产库 v2：草稿 / 个人 / 小组 / 团队 / 回收站",
    permissions=(
        PermissionSpec("library:read", "查看资产库", group="资产库"),
        PermissionSpec("library:create", "上传与封装资产", group="资产库"),
    ),
    # M1 起有运维命令（旧素材导入）；jobs / periodic 仍留到 M2/M3 有实现时再加，
    # 提前声明会让 `create_app()` 的装配对账直接拒绝启动。
    cli=register_cli,
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
    doctor=health_check,
)
