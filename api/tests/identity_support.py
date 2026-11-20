"""测试里造账号时用的共用小工具。

## 为什么需要 `with_pages`

权限点分成「页面」与「接口」两层之后，服务端会做**页面门控**：
接口权限只有在「至少一个声明它的页面被授予」时才有效。
于是测试里造出来的账号如果只授接口码、不授页面码，
所有接口都会 403 —— 断言拿到的是 None，错误信息变成
`TypeError: 'NoneType' object is not subscriptable`，完全看不出真因。

`with_pages()` 按后端目录把「这个接口码属于哪些页面」补上，
与生产环境那次一次性迁移（`migrate_roles_to_pages`）是同一个语义：
**保持升级前的可用性**。

页面门控本身由 `tests/test_pages.py` 专门测 —— 那里要的就是「不给页面就 403」。
"""

from __future__ import annotations

from collections.abc import Iterable


def with_pages(permissions: Iterable[str]) -> list[str]:
    """接口码自动补上它所属的页面码。"""
    from api.discovery import discover_modules
    from core.pages import page_catalog

    if not page_catalog.codes():  # pragma: no cover - 装配早于夹具使用时才会走到
        discover_modules()
    codes = list(permissions)
    if "*" in codes:
        return codes
    pages: set[str] = set()
    for code in codes:
        pages.update(page_catalog.pages_for(code))
    return sorted(set(codes) | pages)


__all__ = ["with_pages"]
