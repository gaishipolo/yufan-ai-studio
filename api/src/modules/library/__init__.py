"""资产库 v2：草稿箱 / 个人 / 小组 / 团队 / 回收站五入口的统一资产库。

**与现役 `modules/asset` 并行的第二版素材库**，不是它的重构。并存期两边
完全隔离：表（`library_*`）、路由（`/library`）、权限（`library:*`）、
注入口（`library-lookup` / `library-ingest` / `library-references`）、
前端路径（`/library`）。切换时只改前端模块的 `load()` 一行，删掉本模块
不影响现役素材库。

M0 只交付骨架：模块注册、11 个权限点、错误码、三个注入口契约、列表壳接口。
M1 建表与上传，M2 可见性/共享/生命周期，M3 封装/下载/入库，M4 前端页面。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.library.health import check as health_check
from modules.library.router import router

# 权限点全部用新前缀 `library:*`，**不进 `BUILTIN_ROLES`**：v2 只有演示账号
# （超管，权限 `*`）能访问，真实角色的授权在切换批次里单独配，避免并存期
# 旧素材库的权限改动顺带改变 v2 的可见范围。
module = ModuleSpec(
    name="library",
    router=router,
    tags=["资产库"],
    description="资产库 v2：草稿 / 个人 / 小组 / 团队 / 回收站",
    permissions=(
        PermissionSpec("library:read", "查看资产库", group="资产库"),
        PermissionSpec("library:create", "上传与封装资产", group="资产库"),
        PermissionSpec("library:update", "编辑本人资产", group="资产库"),
        PermissionSpec("library:delete", "删除本人资产（移入回收站）", group="资产库"),
        PermissionSpec("library:delete:any", "管理任意资产（含他人与团队库）", group="资产库"),
        PermissionSpec("library:draft:review", "审核他人草稿", group="资产库"),
        PermissionSpec("library:share", "发起共享与撤销共享申请", group="资产库"),
        PermissionSpec("library:share:direct", "免审直接共享到小组", group="资产库"),
        PermissionSpec("library:share:approve", "审核共享与撤销申请", group="资产库"),
        PermissionSpec("library:tag:manage", "管理标签（改名 / 合并 / 删除）", group="资产库"),
        PermissionSpec("library:generation:ingest", "生成结果入库", group="资产库"),
    ),
    # M1/M2 才建表与写实现，所以 M0 刻意不声明 jobs / periodic：
    # `create_app()` 会核对「声明了必须注册」，提前声明会让服务起不来。
    doctor=health_check,
)
