"""AI 模型驱动模块：单文件驱动标准 + 自动载入 + 统一生成接口。

对上游（驱动开发者）：在 `drivers/` 放一个实现 `Driver` 的 Python 文件，
定义 submit / poll / cancel 与模型清单，不需要改配置或注册。
对下游（其他业务模块）：`core/ports/model_generation` 是唯一入口，
submit / query / cancel / models 四个方法，底层复用 `generation.runner` 编排。

装配三件事都在本文件 import 期完成：
1. `pipeline` 副作用导入 → 注册 `model_driver` 这个生成 kind；
2. `ensure_drivers_registered()` → 扫描 drivers/ 并接进 `ModelRouter`；
3. `register_model_generation()` → 把门面挂到跨模块注入口。
"""

from api.module import ModuleSpec
from core.ports.model_generation import register_model_generation
from modules.model_driver import pipeline  # noqa: F401  副作用：注册生成流水线
from modules.model_driver.cli import register as register_cli
from modules.model_driver.health import check as health_check
from modules.model_driver.models import KIND_MODEL_DRIVER
from modules.model_driver.permissions import SPECS
from modules.model_driver.registry import bootstrap, ensure_drivers_registered
from modules.model_driver.router import router
from modules.model_driver.service import ModelGenerationFacade

ensure_drivers_registered()
register_model_generation(ModelGenerationFacade())

module = ModuleSpec(
    name="model_driver",
    router=router,
    tags=["模型驱动"],
    description="单文件模型驱动开发与统一生成接口",
    permissions=SPECS,
    recovery_kinds=(KIND_MODEL_DRIVER,),
    bootstrap=bootstrap,
    cli=register_cli,
    doctor=health_check,
)
