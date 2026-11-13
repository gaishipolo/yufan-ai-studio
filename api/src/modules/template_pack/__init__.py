"""封装模板模块：带筛选约束的封装模板。

本文件是模块对外的唯一契约：路由 + 权限点 + 注入口。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。

## 它与 `modules/template` 是什么关系

**没有任何关系。** `modules/template` 是「素材包 + 视频模板」（运行时选**包**），
本模块是「封装模板」（运行时选**单条素材**，且素材要满足发布时声明的筛选条件）。

两者不互相 import（业务模块之间禁止互相 import，AST 静态守卫），
不共享表、不共享路由前缀、不共享权限点前缀。

## 权限点为什么没有 `:delete`

模板是资产：删了会让引用它的编号失效。所以只有「停用」（保留历史、不再对外），
没有「删除」。留着 `:delete` 会在角色配置界面多一个勾了没用的项
（`tests/test_permission_coverage.py` 的红线：声明了却没有任何判定点的权限码会红）。

`:publish` 与 `:use` 分开：发布是作者的动作，使用是消费者的动作 ——
把两者合成一个码，会出现「能用的不能发、能发的不能用」。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.template_pack.router import router

module = ModuleSpec(
    name="template_pack",
    router=router,
    tags=["封装模板"],
    description="封装模板：提示词槽位可声明资产库筛选条件，运行时只允许选合规素材",
    permissions=(
        PermissionSpec("template_pack:read", "查看封装模板", group="内容生产"),
        PermissionSpec("template_pack:create", "创建封装模板", group="内容生产"),
        PermissionSpec("template_pack:update", "编辑未发布的封装模板", group="内容生产"),
        PermissionSpec("template_pack:publish", "发布或停用封装模板", group="内容生产"),
        PermissionSpec("template_pack:use", "使用封装模板选素材", group="内容生产"),
    ),
)

# 供其他模块取用已封装的模板（业务模块之间不许互相 import 的替代通道）。
# 必须 import：注册是副作用，不 import 这行注入口就没接上。
from modules.template_pack import port_impl  # noqa: E402,F401  isort: skip

port_impl.install()
