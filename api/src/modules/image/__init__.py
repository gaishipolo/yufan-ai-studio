"""生图模块：文生图作业的提交与历史。

本文件是模块对外的唯一契约：路由 + 权限点 + 启动钩子。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。
"""

from api.module import ModuleSpec
from core.ports.generation_feed import register_generation_feed
from core.ports.library_references import LibraryReferenceSource, bind_library_reference_sources
from modules.image import (
    maintenance,  # noqa: F401  副作用导入：注册周期任务 image.reconcile
    pipeline,  # noqa: F401  副作用导入：把流水线注册到 generation.pipeline
)
from modules.image.feed_provider import read_generation_feed
from modules.image.health import check as health_check
from modules.image.library_references import ImageJobReferences
from modules.image.permissions import SPECS
from modules.image.router import router
from modules.image.seed import seed

# 参考图快照只冻结 `storageKey`，purge 之后这条作业的重试读不到字节，
# 所以必须声明成 v2 引用来源（漏了 = 删得掉，但重试静默失败）。
# `text_json` 不算：正文已冻结进快照，删资产不影响历史可读。
LIBRARY_REFERENCE_SOURCES = (
    LibraryReferenceSource(
        name="image_jobs.reference_json",
        checker=ImageJobReferences(),
        location="modules/image/models.py:ImageJob.reference_json",
        description="生图作业的参考图快照（存 storageKey，purge 后重试读不到字节）",
    ),
)
bind_library_reference_sources("image", LIBRARY_REFERENCE_SOURCES)

# 任务中心要显示生图记录，但业务模块之间不许互相 import：由本模块在 import 期
# 把只读 reader 交给 core 注入口，任务中心只认 `GenerationFeedPage` 这份契约。
register_generation_feed(read_generation_feed)

module = ModuleSpec(
    name="image",
    router=router,
    tags=["生图"],
    description="生图",
    permissions=SPECS,
    # 演示数据由模块自带：`app seed` 会遍历所有模块的 seed 钩子
    seed=seed,
    seed_order=70,
    # 没有自定义 startup：恢复交给 generation/recovery.py 统一处理。
    # 声明这个 kind 是「模块 × 插槽」矩阵守卫的依据（R11-01）。
    recovery_kinds=(pipeline.KIND_IMAGE,),
    periodic=("image.reconcile",),
    # 部署自检：历史行里还有没有旧 /static 地址（见 modules/image/health.py）
    doctor=health_check,
    library_reference_sources=LIBRARY_REFERENCE_SOURCES,
)
