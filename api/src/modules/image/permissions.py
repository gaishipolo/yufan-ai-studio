"""生图模块的权限点。

`image:read:all` 与 `task:read:all` 是同一个概念：**数据范围开关**。
没有它的时候，列表只按 `owner_id` 过滤，管理员想「看到全部生图历史」
只能靠数据库——同一个工作台里两个模块对「数据范围」给两套答案，
使用者会以为是自己没权限（R4-07 / R6-09）。
"""

from __future__ import annotations

from core.permissions import PermissionSpec

PERM_READ = "image:read"
PERM_CREATE = "image:create"
# 跨用户可见：看别人的生图历史。与 task 模块同名同义，方便角色一次性授权
PERM_READ_ALL = "image:read:all"

SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec(PERM_READ, "查看生图历史", group="内容生产"),
    PermissionSpec(PERM_CREATE, "提交生图任务", group="内容生产"),
    PermissionSpec(
        PERM_READ_ALL,
        "查看他人生图历史",
        group="内容生产",
        description="列表的数据范围开关；与 task:read:all 同义，不含提交他人的生图任务",
    ),
)

__all__ = ["PERM_CREATE", "PERM_READ", "PERM_READ_ALL", "SPECS"]
