"""身份模块错误码（slot 2，见 `docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + 2 × 10 + seq`，因此 `code // 100` 恒等于 HTTP 状态码。
每个 (状态码, seq) 只能用一次，`ModuleErrors` 会在导入期检查。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("identity", slot=2)


class CredentialInvalid(AppError):
    """登录失败。

    用户名不存在和密码错误**共用同一个错误**：区分开会把「这个账号存在」
    这个信息白送给爆破脚本。
    """

    code = errors.code(401, 1)
    message = "用户名或密码错误"
    http_status = 401


class AccountDisabled(AppError):
    code = errors.code(403, 1)
    message = "账号已停用，请联系管理员"
    http_status = 403


class RefreshTokenInvalid(AppError):
    """刷新令牌不能用了：过期、已轮换、会话被吊销、或账号状态变了。

    与 `CredentialInvalid` 分开是因为前端的动作不同：登录失败留在当前页，
    刷新失败要**清掉本地会话回登录页**并提示「登录状态已过期」。
    两者都是 401，但业务码不同，前端按码分流而不是按文案猜。
    """

    code = errors.code(401, 2)
    message = "刷新令牌已失效，请重新登录"
    http_status = 401


class SuperuserRequired(AppError):
    code = errors.code(403, 2)
    message = "该操作需要超级管理员权限"
    http_status = 403


class PermissionEscalation(AppError):
    """想授予自己不具备的权限。

    「能管角色」不等于「能造一个比自己更大的角色」：否则 `identity:role:manage`
    就是一张通往超管的单程票。判定方式见 `IdentityService._guard_grant`。
    """

    code = errors.code(403, 3)
    message = "不能授予自己不具备的权限"
    http_status = 403


class AccessSwitchForbidden(AppError):
    """开关账号访问权（启用 / 停用）需要 `identity:delete`。

    停用与启用是同一个能力的两端，而 `PATCH /users/{id}` 本来只需要
    `identity:update` —— 于是「可以编辑资料、但不能停用」这种角色根本表达不出来，
    两个权限点承诺同一件事。这条判定把两端都收回到 `identity:delete` 名下
    （`DELETE /users/{id}` 与 `PATCH {"isActive": ...}` 同口径）。

    与 `PermissionEscalation` 分开：那个是「授出了自己没有的东西」，
    这个只是「少一个权限点」，前端提示的动作不同（前者改授权，后者找管理员）。
    """

    code = errors.code(403, 4)
    # 面向管理员的中文，别把权限码原样甩到界面上：这条错误只会出现在
    # 「用户与权限」页，读它的人需要知道该去要哪个开关，而不是记一个 code。
    message = "停用或启用账号需要「停用用户」权限"
    http_status = 403


class UserNotFound(AppError):
    code = errors.code(404, 1)
    message = "用户不存在"
    http_status = 404


class RoleNotFound(AppError):
    code = errors.code(404, 2)
    message = "角色不存在"
    http_status = 404


class SessionNotFound(AppError):
    """会话不存在，或已失效（登出 / 踢下线 / 过期）。

    三种情况合并成一个 404：区分开等于告诉调用方「这个 sid 存在但被踢了」，
    而调用方本来就只该处理「用不了」这一种结局。
    """

    code = errors.code(404, 3)
    message = "会话不存在或已失效"
    http_status = 404


class GroupNotFound(AppError):
    code = errors.code(404, 4)
    message = "小组不存在"
    http_status = 404


class GroupNameTaken(AppError):
    code = errors.code(409, 7)
    message = "小组名称已被占用"
    http_status = 409


class SuperuserGrantForbidden(AppError):
    """超管的权限恒为 `*`，任何层的授权都到不了判定里。

    为什么要在写入口拒：`resolve_identity` 见到 `*` 就短路，所以给超管配
    角色权限 / 个人权限**不会生效**，但会留在库里、显示在界面上 ——
    于是管理员以为自己配了什么（2026-11-20 就是这么发现的：角色页禁了编辑，
    用户页的个人权限却还能改）。无效的写操作不该被接受，也不该只在某一侧拦。
    """

    code = errors.code(409, 8)
    message = "超级管理员的权限恒为全部，不能在角色或个人层配置"
    http_status = 409


class TeamGrantForbidden(AppError):
    """团队层 = 所有人：管理类权限、页面权限与 `*` 不能放进来。

    这不是「权限码写错了」，所以不与 `UnknownPermission` 合并——
    前者是配置约束，后者是目录里根本没有这个码，前端提示的动作不同。
    """

    code = errors.code(400, 3)
    message = "团队基础权限不能包含管理类权限、页面权限或通配符"
    http_status = 400


class ExceptionWildcardForbidden(AppError):
    """例外只能是目录里的具体码。

    为什么拒绝通配：`task:*` 的「关掉」没有唯一解释 —— 是关掉现在这几个动作，
    还是连以后新加的也关？两种都能自圆其说，而库里存的那一条说不清。
    界面上每个例外都对应一行具体权限，也不需要通配。
    """

    code = errors.code(400, 4)
    message = "例外只能是具体的权限码，不能写通配"
    http_status = 400


class ExceptionLockedPermission(AppError):
    """组织管理类权限（`identity:*`）不能被例外关掉。

    与「团队层禁 `identity:*`」同一条理由：**不可逆**。管理员把自己
    `identity:user:permission:manage` 关掉之后，这个页面就打不开了，
    只能改库才能恢复 —— 一个能被自己锁死的开关不该存在。
    """

    code = errors.code(400, 5)
    message = "例外不能作用于组织管理类权限（identity:*），否则会把管理员锁在门外"
    http_status = 400


class UsernameTaken(AppError):
    code = errors.code(409, 1)
    message = "用户名已被占用"
    http_status = 409


class RoleCodeTaken(AppError):
    code = errors.code(409, 2)
    message = "角色标识已存在"
    http_status = 409


class BuiltinRoleLocked(AppError):
    code = errors.code(409, 3)
    message = "内置角色不可删除，标识也不可修改"
    http_status = 409


class RoleInUse(AppError):
    code = errors.code(409, 4)
    message = "该角色仍被用户使用，请先解除绑定"
    http_status = 409


class CannotTargetSelf(AppError):
    code = errors.code(409, 5)
    message = "不能停用或删除自己的账号"
    http_status = 409


class LastSuperuser(AppError):
    code = errors.code(409, 6)
    message = "系统必须保留至少一个启用的超级管理员"
    http_status = 409


class OldPasswordMismatch(AppError):
    code = errors.code(400, 1)
    message = "原密码不正确"
    http_status = 400


class UnknownPermission(AppError):
    code = errors.code(400, 2)
    message = "存在无法识别的权限点"
    http_status = 400


__all__ = [
    "AccessSwitchForbidden",
    "AccountDisabled",
    "BuiltinRoleLocked",
    "CannotTargetSelf",
    "CredentialInvalid",
    "ExceptionLockedPermission",
    "ExceptionWildcardForbidden",
    "GroupNameTaken",
    "GroupNotFound",
    "LastSuperuser",
    "OldPasswordMismatch",
    "PermissionEscalation",
    "RefreshTokenInvalid",
    "TeamGrantForbidden",
    "RoleCodeTaken",
    "RoleInUse",
    "RoleNotFound",
    "SessionNotFound",
    "SuperuserRequired",
    "UnknownPermission",
    "UsernameTaken",
    "UserNotFound",
    "errors",
]
