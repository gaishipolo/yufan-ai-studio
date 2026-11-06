"""生图模块：文生图作业的提交与历史。"""

from api.module import ModuleSpec
from modules.image import (
    maintenance,  # noqa: F401  副作用导入：注册周期任务 image.reconcile
    pipeline,  # noqa: F401  副作用导入：把流水线注册到 generation.pipeline
)
from modules.image.permissions import SPECS
from modules.image.router import router
from modules.image.seed import seed

module = ModuleSpec(
    name="image",
    router=router,
    tags=["生图"],
    permissions=SPECS,
    # 演示数据由模块自带：`app seed` 会遍历所有模块的 seed 钩子
    seed=seed,
    # 没有自定义 startup：恢复交给 generation/recovery.py 统一处理。
    # 声明这个 kind 是「模块 × 插槽」矩阵守卫的依据（R11-01）。
    recovery_kinds=(pipeline.KIND_IMAGE,),
)
