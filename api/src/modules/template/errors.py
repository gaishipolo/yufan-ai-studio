"""模板错误码（slot 7，见 `docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + 7 × 10 + seq`，
因此 `code // 100` 恒等于 HTTP 状态码。每个 (状态码, seq) 在一个模块内只能用一次，
冲突会在导入期由 `ModuleErrors` 直接报错，不会静默覆盖。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("template", slot=7)


class TemplateBusy(AppError):
    """示例错误码：删掉它，换成你真正需要的（每个状态码最多 9 个）。"""

    code = errors.code(409, 1)
    message = "模板正在处理中，请稍后再试"
    http_status = 409
