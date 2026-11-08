"""路由表的展开工具：把 FastAPI 的「延迟包含」摊平成真实路由。

## 为什么需要它

FastAPI 0.162 起 `include_router()` 不再把子路由摊平进 `app.routes` / 父 router 的
`routes`，而是放一个 `_IncludedRouter` 占位，包着原 router 和 include 前缀。

这带来一种**特别隐蔽的失败模式**：任何「遍历路由做检查」的守卫，如果只 `for route
in router.routes`，在遇到 `include_router` 时会看到一个占位对象，它既不是 `APIRoute`
也没有 `.dependant`，于是守卫的循环体一次都不执行——**检查永远通过，而不是报错**。
比没有守卫更糟：它提供的是「已经查过了」的错觉。

本项目踩过两次：`api/ratelimit_policy.py` 的写路由策略表（当时发现了，写了本函数），
和 `api/assembly.py` 的权限核对（当时没发现，`template` 模块的 20 条路由从未被核对）。

## 用法

- `iter_api_routes(routes)`：`(APIRoute, 完整路径)`，自动累加 include 前缀。
- 传 `app.routes` 或任意 `ModuleSpec.router.routes`；**不要**再自己加初始前缀。
- 不依赖私有类名：靠 `original_router` 属性判型，FastAPI 换实现也还能用。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from fastapi.routing import APIRoute

__all__ = ["iter_api_routes"]


def _included(route: Any) -> Any:
    """判断是不是 FastAPI 的「延迟包含」包装（不同版本形态不同）。"""
    return route if hasattr(route, "original_router") else None


def iter_api_routes(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[APIRoute, str]]:
    """递归展开被 include 的 router，产出 `(路由, 完整路径)`。"""
    for route in routes:
        wrapped = _included(route)
        if wrapped is not None:
            context = getattr(wrapped, "include_context", None)
            extra = getattr(context, "prefix", "") or ""
            yield from iter_api_routes(wrapped.original_router.routes, prefix + extra)
        elif isinstance(route, APIRoute):
            yield route, prefix + route.path
