"""生图模块的权限点。

`image:read:all` 与 `task:read:all` 是同一个概念：**数据范围开关**。
没有它的时候，列表只按 `owner_id` 过滤，管理员想「看到全部生图历史」
只能靠数据库——同一个工作台里两个模块对「数据范围」给两套答案，
使用者会以为是自己没权限（R4-07 / R6-09）。

**为什么不是脚手架给的四个 CRUD 权限**：生图**没有「编辑」**这个动作；「删除」是有
的（2026-11-15 补，见 `PERM_DELETE`），但它的语义是「清掉这条生成历史」，不是编辑
记录内容。照抄 `:update` 会让角色配置界面多一个永远勾不到任何东西的权限。

`PERM_DELETE` 为什么现在才加：任务中心 2026-11-13 接入生图时只做了读 / 搜索 / 重试，
删除走的是 task 模块自己的 `DELETE /tasks`（只认 `video_tasks`）。用户 2026-11-15
明确「直接让生图任务也能删」，于是补齐：记录删了就不再可重试，这一点用户已知并接受。
"""

from __future__ import annotations

from core.permissions import PermissionSpec

PERM_READ = "image:read"
PERM_CREATE = "image:create"
# 跨用户可见：看别人的生图历史。与 task 模块同名同义，方便角色一次性授权
PERM_READ_ALL = "image:read:all"
# 与 task:retry:any 同义：允许客服替别人重跑，但不蕴含 read:all（不能看别人的 prompt）
PERM_RETRY_ANY = "image:retry:any"
# 删除生成历史（含在飞的记录）。删掉之后这条记录不再可重试：它是「放弃」，
# 不是「隐藏」，所以需要独立的码，不能靠 `image:create` 顺带获得。
PERM_DELETE = "image:delete"

SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec(PERM_READ, "查看生图历史", group="内容生产"),
    PermissionSpec(PERM_CREATE, "提交生图任务", group="内容生产"),
    PermissionSpec(
        PERM_READ_ALL,
        "查看他人生图历史",
        group="内容生产",
        description="列表的数据范围开关；与 task:read:all 同义，不含提交他人的生图任务",
    ),
    PermissionSpec(
        PERM_RETRY_ANY,
        "重试他人生图任务",
        group="内容生产",
        description="只开关「替别人点重试」，不蕴含 image:read:all",
    ),
    PermissionSpec(
        PERM_DELETE,
        "删除生图记录",
        group="内容生产",
        description=(
            "删除生成历史，删除后不可再重试；"
            "不含删除他人记录（那需要 image:read:all 的数据范围）"
        ),
    ),
)

__all__ = [
    "PERM_CREATE",
    "PERM_DELETE",
    "PERM_READ",
    "PERM_READ_ALL",
    "PERM_RETRY_ANY",
    "SPECS",
]
