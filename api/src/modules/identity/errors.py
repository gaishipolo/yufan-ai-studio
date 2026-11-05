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


class UserNotFound(AppError):
    code = errors.code(404, 1)
    message = "用户不存在"
    http_status = 404


class RoleNotFound(AppError):
    code = errors.code(404, 2)
    message = "角色不存在"
    http_status = 404


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
    "AccountDisabled",
    "BuiltinRoleLocked",
    "CannotTargetSelf",
    "CredentialInvalid",
    "LastSuperuser",
    "OldPasswordMismatch",
    "PermissionEscalation",
    "RoleCodeTaken",
    "RoleInUse",
    "RoleNotFound",
    "SuperuserRequired",
    "UnknownPermission",
    "UsernameTaken",
    "UserNotFound",
    "errors",
]
