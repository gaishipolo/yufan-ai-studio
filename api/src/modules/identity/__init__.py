"""身份模块：用户、角色、权限绑定，以及登录。

本文件是模块对外的唯一契约：路由 + 权限点 + 启动钩子 + **主体解析器注册**。

`register_principal_resolver` 这一行是模块 import 期的副作用，和 `task` 模块注册
生成流水线是同一个套路：模块自己把能力接进框架，核心不认识这个模块。
删掉它不会报错，但权限会退回「信任令牌里的快照」——停用账号的令牌会继续有效。
`tests/test_identity_api.py::test_deactivation_takes_effect_immediately` 专门守这条。
"""

from api.authn import register_principal_resolver
from api.module import ModuleSpec
from core.ports.accounts import register_account_status_checker
from modules.identity.account_status import IdentityAccountStatus
from modules.identity.permissions import SPECS
from modules.identity.principal import IdentityPrincipalResolver
from modules.identity.router import router

# 必须在模块被 import 时注册（发现流程 import 本包即生效）
register_principal_resolver(IdentityPrincipalResolver())
# 无请求上下文的调用方（后台任务 / CLI）靠这条判断账号是否还能干活
register_account_status_checker(IdentityAccountStatus())


async def _bootstrap() -> None:
    """启动时确保内置角色存在，并把新增权限点并进去（幂等）。

    走 `bootstrap` 而不是 `startup`：这是装配动作，不改写任何「正在进行的业务
    状态」，因此**每个实例都该跑**。塞进 `startup` 的话，`WORKER_ENABLED=false`
    的 API 实例永远不会初始化角色、也永远补不到新权限点——那种坏是静默的。
    """
    from modules.identity.service import bootstrap

    await bootstrap()


module = ModuleSpec(
    name="identity",
    router=router,
    tags=["身份"],
    description="用户与角色：登录、账号管理、权限点目录",
    permissions=SPECS,
    bootstrap=_bootstrap,
)
