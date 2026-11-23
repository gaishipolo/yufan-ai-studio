"""用量看板的权限点。

两个码，一层拆分：

- `usage:read` —— 看**自己**的用量。绝大多数人只有这一个。
- `usage:read:all` —— 看**全部成员**的用量（数据范围可以切到「全部」）。

为什么不用「一个 `usage:read` + 一个隐藏参数」的做法：看板的「全部成员」视图
天然是管理视角，把管理权限和查看自己的权限分开，才能让「谁都能看自己的用量」
这件事不需要任何额外授权。
"""

from core.permissions import PermissionSpec

#: 页面码。用量看板页本身也是一个权限点，授予它 = 侧栏里能看见这一页。
PAGE = "page:usage"

#: 查看用量看板（本人范围）。
READ = "usage:read"

#: 查看全部成员的用量。
READ_ALL = "usage:read:all"

SPECS = (
    PermissionSpec(
        code=READ,
        name="查看用量看板",
        group="数据统计",
        description="查看自己在指定区间内的生成用量（次数 / 时长 / 张数）",
        pages=(PAGE,),
    ),
    PermissionSpec(
        code=READ_ALL,
        name="查看全部用量",
        group="数据统计",
        description="用量看板的数据范围可以切到「全部成员」，并按使用人查看构成",
        pages=(PAGE,),
    ),
)

__all__ = ["PAGE", "READ", "READ_ALL", "SPECS"]
