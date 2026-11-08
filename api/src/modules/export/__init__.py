"""导出模块。

本文件是模块对外的唯一契约：路由 + 权限点。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。

权限点只有三个：导出没有「改」——一份已经生成的数据快照，改它的意义只能是
重新导一次（那是 create）。删掉 `export:update` 不是省事，是别让角色配置界面
多一个永远用不上的开关。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.export.router import router

# 副作用导入：注册 job handler（export.build）与周期任务（export.reclaim / export.stale）。
# 漏了这两行，导出会永远停在 queued——而 `app modules` 里同样看不到周期任务，
# 没有任何一处会主动报错。这是新模块最容易漏的一步。
from modules.export import maintenance, worker  # noqa: E402,F401  isort: skip

module = ModuleSpec(
    name="export",
    router=router,
    tags=["导出"],
    description="数据导出：按数据源异步生成 CSV，产物到期自动回收",
    permissions=(
        PermissionSpec("export:read", "查看导出", group="内容生产"),
        PermissionSpec("export:create", "创建导出", group="内容生产"),
        PermissionSpec("export:delete", "删除导出", group="内容生产"),
        # 操作留痕含他人行为，单独一个码：它只发给运维/审计，
        # 和「能不能导自己的任务」是两回事。
        PermissionSpec("export:read:audit", "导出操作留痕", group="系统"),
    ),
    jobs=("export.build",),
    periodic=("export.reclaim", "export.stale"),
)
