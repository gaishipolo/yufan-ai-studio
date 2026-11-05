"""任务模块的权限点：**单一常量**，避免权限码在业务代码里散落成字符串。

`task:read:all` 是本模块唯一的「数据范围开关」：
列表的 `scope=all`、详情、删除都只判断它，不再判断角色名。

新增权限点只改本文件 + `ModuleSpec`，`api/` 与 `core/` 不需要任何改动。
"""

from __future__ import annotations

from core.permissions import PermissionSpec

PERM_READ = "task:read"
PERM_CREATE = "task:create"
PERM_UPDATE = "task:update"
PERM_DELETE = "task:delete"
# 跨用户可见：看别人的任务、进别人的详情、删别人的任务，都是这一个开关
PERM_READ_ALL = "task:read:all"

SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec(PERM_READ, "查看任务", group="内容生产"),
    PermissionSpec(PERM_CREATE, "创建生成任务", group="内容生产"),
    PermissionSpec(PERM_UPDATE, "编辑/重试任务", group="内容生产"),
    PermissionSpec(PERM_DELETE, "删除任务", group="内容生产"),
    PermissionSpec(
        PERM_READ_ALL,
        "查看并管理他人任务",
        group="内容生产",
        description="列表 scope=all / 详情 / 删除的跨用户判定依据",
    ),
)

__all__ = [
    "PERM_CREATE",
    "PERM_DELETE",
    "PERM_READ",
    "PERM_READ_ALL",
    "PERM_UPDATE",
    "SPECS",
]
