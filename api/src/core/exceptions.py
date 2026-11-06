"""统一异常体系。

设计要点：
1. 业务错误码 `code` 与 HTTP 状态码绑定：`http_status = code // 100`，
   既保证前端拿到可判定的业务码，又保持 HTTP 语义正确。
2. 所有可预期错误都以 `AppError` 子类抛出，路由层无需 try/except，
   由 `api/errors.py` 的全局处理器统一转换为 `{code, message, data}`。
3. 模块可自带错误码号段（`ModuleErrors`），新增业务异常**不需要修改本文件**。
"""

from __future__ import annotations

from typing import Any

from core.registry import RegistryError


class ErrorCode:
    """框架级（slot 0）错误码。

    编号规则：`HTTP状态码 * 100 + 序号`，最后两位为同类错误的细分。
    每个 HTTP 段预留 100 个位置，其中：
    - `00-09`：框架/通用（本类）
    - `10-99`：业务模块，按 `ModuleErrors` 分配的 slot 使用

    前端只判断 `code == 0` 为成功，其余一律按 `message` 展示。
    """

    OK = 0

    # 400 参数/业务校验
    BAD_REQUEST = 40_000
    VALIDATION_FAILED = 40_001
    UNSUPPORTED_PARAM = 40_003

    # 401 未认证
    UNAUTHORIZED = 40_100
    TOKEN_EXPIRED = 40_101
    TOKEN_INVALID = 40_102

    # 403 无权限
    FORBIDDEN = 40_300

    # 404 资源不存在
    NOT_FOUND = 40_400

    # 409 状态冲突
    CONFLICT = 40_900
    RESOURCE_BUSY = 40_901
    INVALID_STATE = 40_902

    # 429 限流
    RATE_LIMITED = 42_900

    # 500 服务端
    INTERNAL_ERROR = 50_000
    DATABASE_ERROR = 50_001

    # 501 未实现（脚手架阶段的占位）
    NOT_IMPLEMENTED = 50_100

    # 502 上游依赖
    UPSTREAM_ERROR = 50_200

    # 503 本服务暂不可用（配置/容量/维护），与「上游挂了」区分开
    UNAVAILABLE = 50_300


class ModuleErrors:
    """模块级错误码生成器。

    团队成员各自占用一个 `slot`（见 `docs/EXTENDING.md` 的号段分配表），
    在自己的段内编号，互不干扰：

        errors = ModuleErrors("voice", slot=3)

        class VoiceBusy(AppError):
            code = errors.code(409, 1)      # -> 40931
            message = "音色正在克隆中"
            http_status = 409

    约束：`slot` 只能取 1-9，`seq` 只能取 1-9，保证
    `code // 100 == http_status` 恒成立、且不会越界到下一个段。
    """

    __slots__ = ("module", "slot", "_used")

    def __init__(self, module: str, slot: int) -> None:
        if not 1 <= slot <= 9:
            raise ValueError(f"slot 必须为 1-9（0 为框架保留），收到 {slot}")
        self.module = module
        self.slot = slot
        self._used: dict[int, int] = {}

    def code(self, http_status: int, seq: int) -> int:
        if not 1 <= seq <= 9:
            raise ValueError(f"seq 必须为 1-9，收到 {seq}")
        if not 400 <= http_status <= 599:
            raise ValueError(f"http_status 必须为 4xx/5xx，收到 {http_status}")

        value = http_status * 100 + self.slot * 10 + seq
        existing = self._used.get(value)
        if existing is not None:
            raise RegistryError(
                f"{self.module} 错误码重复 {value}: seq {seq} 已被占用"
            )
        self._used[value] = seq
        return value


class AppError(Exception):
    """所有业务异常的基类。"""

    code: int = ErrorCode.INTERNAL_ERROR
    message: str = "服务器内部错误"
    http_status: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        code: int | None = None,
        http_status: int | None = None,
        details: Any = None,
    ) -> None:
        self.code = code or type(self).code
        self.message = message or type(self).message
        self.http_status = http_status or type(self).http_status
        self.details = details
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message, "data": None}
        if self.details is not None:
            payload["details"] = self.details
        return payload

    @property
    def retryable(self) -> bool:
        """这个错误值不值得重试。

        4xx 是「调用方写错了 / 没权限 / 资源不存在」，重试一万次也是同样的结果，
        只会让用户多等几次退避、并且用最后一次的错误覆盖真正的根因。
        429 与 5xx 属于临时状态，可以退避重试——但 **501 除外**：它是
        `NotImplementedFeature`，代码里就没写这个功能，重试三次只是白等 6 秒。
        """
        if self.http_status == 501:
            return False
        return self.http_status == 429 or self.http_status >= 500


# --------------------------------------------------------------------------- #
# 语义化子类：各层直接 `raise NotFound("任务不存在")` 即可
# --------------------------------------------------------------------------- #
class BadRequest(AppError):
    code = ErrorCode.BAD_REQUEST
    message = "请求参数有误"
    http_status = 400


class ValidationFailed(BadRequest):
    code = ErrorCode.VALIDATION_FAILED
    message = "参数校验失败"


class InvalidState(AppError):
    code = ErrorCode.INVALID_STATE
    message = "当前状态不允许该操作"
    http_status = 409


class Unauthorized(AppError):
    code = ErrorCode.UNAUTHORIZED
    message = "请先登录"
    http_status = 401


class Forbidden(AppError):
    code = ErrorCode.FORBIDDEN
    message = "没有操作权限"
    http_status = 403


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND
    message = "资源不存在"
    http_status = 404


class Conflict(AppError):
    code = ErrorCode.CONFLICT
    message = "资源冲突"
    http_status = 409


class RateLimited(AppError):
    code = ErrorCode.RATE_LIMITED
    message = "请求过于频繁，请稍后再试"
    http_status = 429


class UpstreamError(AppError):
    code = ErrorCode.UPSTREAM_ERROR
    message = "上游服务异常"
    http_status = 502


class Unavailable(AppError):
    """本服务暂时不接活：后台执行器未启用、容量已满、正在维护。

    不能用 502 表达：那等于告诉前端与运维「供应商出问题了」，而实际是
    本机配置（`WORKER_ENABLED=false`）——去查 LibTV 只会白查一圈（T5）。
    """

    code = ErrorCode.UNAVAILABLE
    message = "服务暂不可用，请稍后再试"
    http_status = 503


class NotImplementedFeature(AppError):
    """脚手架占位：接口已注册但业务逻辑未实现。

    每个未完成的 service 方法都抛它，前端会拿到统一的 501 + 明确文案，
    不会误判成崩溃。
    """

    code = ErrorCode.NOT_IMPLEMENTED
    message = "该功能尚未实现"
    http_status = 501
