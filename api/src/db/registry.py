"""模型注册表：自动扫描所有业务模块的 ORM 实体。

历史版本是一份手写的 import 列表，新增模块必须在这里加一行 ——
一旦忘记，症状是「表不存在」这种很难联想到注册表的报错。

现在改为扫描 `modules.*.models`，新增模块的表会自动进入
`Base.metadata`，`create_all` 与 Alembic autogenerate 都能感知。

**框架自带的表在 `db/` 里（如 `audit_events`），不在 `modules/` 下**，
所以扫描之外还要显式导入 `FRAMEWORK_MODELS`。漏掉它们的症状很隐蔽：
运行时因为某个业务代码顺手 import 了 `db.audit` 而一切正常，`alembic check`
却看不到这张表，于是报「检测到删除表 audit_events」——真按它跑 autogenerate，
生产库的审计数据就没了。

注意依赖方向：本文件属于 db 层，因此**不能** import `api.discovery`，
否则 db 会反向依赖 api。这里直接用 pkgutil 扫描。
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

from core.logging import get_logger
from db.base import Base

logger = get_logger(__name__)

MODULES_PACKAGE = "modules"

# 不属于任何业务模块、但确实建表的框架层模型
FRAMEWORK_MODELS = ("db.audit",)


@lru_cache
def load_all_models(package_name: str = MODULES_PACKAGE) -> tuple[str, ...]:
    """导入所有模块的 models，返回已加载的模块路径。"""
    package = importlib.import_module(package_name)
    loaded: list[str] = []

    for name in FRAMEWORK_MODELS:
        importlib.import_module(name)
        loaded.append(name)

    for info in pkgutil.iter_modules(package.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        qualified = f"{package_name}.{info.name}.models"
        try:
            importlib.import_module(qualified)
        except ModuleNotFoundError as exc:
            if exc.name == qualified:
                continue  # 该模块没有 ORM 实体
            raise
        loaded.append(qualified)

    return tuple(loaded)


def metadata_tables() -> list[str]:
    load_all_models()
    return sorted(Base.metadata.tables)


__all__ = ["Base", "load_all_models", "metadata_tables"]
