"""驱动解析：按命名约定动态加载基础设施实现。

约定 —— `infra.<kind>.<name>` 模块暴露 `Driver`（类或已实例化的对象）：

    infra/storage/local.py     Driver = LocalStorage
    infra/queue/redis.py       Driver = RedisJobQueue
    infra/ai/volcengine.py     Driver = VolcengineProvider()

新增一个驱动 = 新增一个文件 + 改 `.env` 里的名字。工厂函数、`if/elif` 全都不需要动。

未实现时**故意抛错而不是降级**：生产环境把 STORAGE_DRIVER 写成 s3 却没实现，
必须启动失败，而不是悄悄落回本地磁盘。
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from core.registry import RegistryError


def _import_driver_module(kind: str, name: str) -> ModuleType:
    # `isidentifier()` 而不是 `isalnum()`：后者对 `中文`、`驱动1` 都返回 True
    # （Unicode 字母算 alnum），于是校验通过、import 失败，最后给出
    # 「请创建 src/infra/storage/中文.py」这种永远不可能成立的提示。
    # `isascii()` 再挡一层：模块名是契约的一部分，会进 .env 与日志，
    # 全项目只允许 ASCII。
    if not (name.isidentifier() and name.isascii()):
        raise RegistryError(f"非法的驱动名 {name!r}（只允许字母、数字、下划线，且不能以数字开头）")

    path = f"infra.{kind}.{name}"
    try:
        return importlib.import_module(path)
    except ModuleNotFoundError as exc:
        if exc.name == path:
            raise NotImplementedError(
                f"驱动 {kind}={name!r} 尚未实现：请创建 src/infra/{kind}/{name}.py，"
                f"并在其中定义 Driver = <实现类>"
            ) from exc
        raise


def load_driver(kind: str, name: str) -> Any:
    """加载并实例化一个驱动。"""
    module = _import_driver_module(kind, name)

    factory = getattr(module, "Driver", None)
    if factory is None:
        raise TypeError(
            f"{module.__name__} 缺少 Driver 定义（可以是类、工厂函数或已实例化对象）"
        )

    # 类 → 实例化；已实例化的对象 → 直接用
    return factory() if isinstance(factory, type) or callable(factory) else factory
