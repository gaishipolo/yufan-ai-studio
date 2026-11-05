"""v1 路由聚合。

只暴露**工厂函数**，不在 import 期构建路由：
模块级的 `v1_router = build_v1_router()` 会让「模块发现」在任意一次 import
时就跑一遍，副作用不可控（测试里尤其明显），而且是一份没人使用的死代码。
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter

from api.discovery import active_modules


def build_v1_router() -> APIRouter:
    router = APIRouter()
    for module in active_modules():
        # include_router 的 tags 声明为 list[str | Enum]，list 是不变型，
        # 直接传 list[str] 会被 mypy 判为不兼容，因此显式标注
        tags: list[str | Enum] = list(module.tags or [module.name])
        # 模块自己的 APIRouter 通常已经声明过 tags，include_router 再传一次
        # 会被 FastAPI **追加**而不是去重，OpenAPI 里就出现 ["任务", "任务"]
        # 两个同名分组。只在模块没声明时才补默认值。
        extra_tags = [] if module.router.tags else tags
        router.include_router(module.router, tags=extra_tags)
    return router


__all__ = ["build_v1_router"]
