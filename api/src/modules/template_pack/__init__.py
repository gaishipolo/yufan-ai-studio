"""模板封装模块：manifest 配置 + 素材包 + 视频模板。

本文件是模块对外的唯一契约：路由 + 权限点 + 注入口。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。

## 迁移阶段 C1 的过渡状态

用户拍板「按 `template_packager` 的能力完整迁移，原有 `template_pack` 功能
可以覆盖」。本阶段先做**纯增量**：

- 新增 `/template-manifests`（配置预检 / 幂等保存 / 发布 / 停用），
  `core.ports.template_catalog` 对外提供已发布数据；
- 旧的 `/template-packs`（带筛选约束的封装模板）**暂时保留**，因为前端还有
  对应页面在用它。阶段 C2 会把 `modules/template` 的素材包 + 视频模板搬进来，
  届时两套路由、权限点、端口一起收敛。

## 职责边界（用户纠正过的架构原则）

**模板封装模块只负责把封装数据管好、通过 ports 对外提供。** 生成是
`modules/task` 的消费行为，不在本模块实现、也不由本模块去接。
`TemplateCatalogPort` 因此是只读的窄接口：没有 `verify_picks`、没有候选素材
判定，更没有「提交生成任务」。

## 权限点为什么没有 `:delete`

模板是资产：删了会让引用它的编号失效。所以只有「停用」（保留历史、不再对外），
没有「删除」。留着 `:delete` 会在角色配置界面多一个勾了没用的项
（`test_permission_coverage.py` 的红线：声明了却没有任何判定点的权限码会红）。

`:publish` 与 `:use` 分开：发布是作者的动作，使用是消费者的动作。
"""

from fastapi import APIRouter

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.template_pack.manifest_router import router as manifest_router
from modules.template_pack.router import router as legacy_router

router = APIRouter()
router.include_router(legacy_router)
router.include_router(manifest_router)

module = ModuleSpec(
    name="template_pack",
    router=router,
    tags=["模板封装"],
    description="模板封装：manifest 配置 DB 化、素材引用资产库，对外经 ports 提供已发布数据",
    permissions=(
        PermissionSpec("template_pack:read", "查看模板封装", group="内容生产"),
        PermissionSpec("template_pack:create", "创建或保存模板封装草稿", group="内容生产"),
        PermissionSpec("template_pack:update", "编辑未发布的模板封装", group="内容生产"),
        PermissionSpec("template_pack:publish", "发布或停用模板封装", group="内容生产"),
        PermissionSpec("template_pack:use", "使用模板封装", group="内容生产"),
    ),
)

# 两个注入口的注册都是 import 期副作用：不 import 这两行，端口就永远没实现。
# `port_impl` 是旧 `/template-packs` 的端口（C2 下线）；
# `catalog_port_impl` 是新的只读目录端口。
from modules.template_pack import catalog_port_impl, port_impl  # noqa: E402,F401  isort: skip

port_impl.install()
catalog_port_impl.install()
