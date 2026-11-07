"""任务模块：视频生成任务的创建、查询、重试与删除。

分层：router（协议）→ service（业务）→ repository（SQL）→ models（ORM）

模块对外的全部信息都在这一个 `ModuleSpec` 里：路由、权限点、启动钩子。
`api/discovery.py` 扫描到它就会自动挂载，不需要修改任何核心文件。
"""

from api.module import ModuleSpec
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources
from modules.task.asset_references import TaskAssetReferences
from modules.task.permissions import SPECS
from modules.task.router import router

# 必须 import：注册到生成框架的 pipeline 表（副作用导入）
from modules.task import pipeline  # noqa: E402,F401  isort: skip
from modules.task.worker import resume_unfinished_tasks  # noqa: E402

# 素材删除前问「还有谁在用」：本模块把 `assets.id` 写进 video_tasks 的这一列。
# 声明是同一份常量：ModuleSpec 供框架对账，import 期注册供运行时聚合。
ASSET_REFERENCE_SOURCES = (
    AssetReferenceSource(
        name="video_tasks.reference_asset_ids",
        checker=TaskAssetReferences(),
        location="modules/task/models.py:VideoTask.reference_asset_ids",
        description="视频生成任务的参考图",
    ),
)
bind_asset_reference_sources("task", ASSET_REFERENCE_SOURCES)


async def _startup() -> None:
    """进程启动时恢复上次没跑完的任务。"""
    from infra import get_queue

    await resume_unfinished_tasks(get_queue())


module = ModuleSpec(
    name="task",
    router=router,
    tags=["任务"],
    description="视频生成任务：创建、查询、重试、收藏、删除",
    permissions=SPECS,
    startup=_startup,
    # 已有自己的恢复器（worker.resume_unfinished_tasks），框架恢复器会跳过，
    # 但声明负责的 kind 让架构守卫能画出完整的「模块 × 插槽」矩阵。
    recovery_kinds=(pipeline.KIND_VIDEO,),
    asset_reference_sources=ASSET_REFERENCE_SOURCES,
)
