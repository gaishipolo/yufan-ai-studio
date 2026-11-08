"""生图模块：文生图作业的提交与历史。

本文件是模块对外的唯一契约：路由 + 权限点 + 启动钩子。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。
"""

from api.module import ModuleSpec
from modules.image import (
    maintenance,  # noqa: F401  副作用导入：注册周期任务 image.reconcile
    pipeline,  # noqa: F401  副作用导入：把流水线注册到 generation.pipeline
)
from modules.image.health import check as health_check
from modules.image.permissions import SPECS
from modules.image.router import router
from modules.image.seed import seed

module = ModuleSpec(
    name="image",
    router=router,
    tags=["生图"],
    description="生图",
    permissions=SPECS,
    # 演示数据由模块自带：`app seed` 会遍历所有模块的 seed 钩子
    seed=seed,
    # 没有自定义 startup：恢复交给 generation/recovery.py 统一处理。
    # 声明这个 kind 是「模块 × 插槽」矩阵守卫的依据（R11-01）。
    recovery_kinds=(pipeline.KIND_IMAGE,),
    periodic=("image.reconcile",),
    # 部署自检：历史行里还有没有旧 /static 地址（见 modules/image/health.py）
    doctor=health_check,
)
