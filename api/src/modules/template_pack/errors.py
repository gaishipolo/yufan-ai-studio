"""封装模板错误码（slot 3，见 `docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + 3 × 10 + seq`，
因此 `code // 100` 恒等于 HTTP 状态码。每个 (状态码, seq) 在一个模块内只能用一次，
冲突会在导入期由 `ModuleErrors` 直接报错，不会静默覆盖。

## 为什么只有两个错误码

脚手架默认给的是通用 CRUD 四件套（`read`/`create`/`update`/`delete` + 一个
示例 `409`）。本模块的真实语义是「发布 → 使用」，没有 delete 动作
（模板是资产，删了会让人引用的编号失效），所以：

- **不声明 `:delete` 权限点** —— 留着它，角色配置界面会多一个勾了没用的项
  （`tests/test_permission_coverage.py` 的红线）。
- **不留示例错误码** —— `40931` 会进 `API.md` 的错误码总表，前端会为一个
  永远不出现的码写分支。
- 只保留真正要区分的两个：**配置冲突**（同键异配置）与**状态不允许**
  （已发布后改内容、重复发布）。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("template_pack", slot=3)


class TemplatePackConflict(AppError):
    """同一个 `templateKey` 被另一份配置占用。

    幂等的另一半：同键**同**配置返回原行（脚本重试不产生副本），
    同键**异**配置才冲突 —— 静默覆盖会让重试把已发布的模板改掉。
    """

    code = errors.code(409, 1)
    message = "该稳定标识已被另一份配置使用，请换一个"
    http_status = 409


class TemplatePackStateError(AppError):
    """当前状态不允许这个动作。

    已发布 / 已停用的模板不允许再改内容：它可能已经被别人引用，
    原地改会让「模板当时长什么样」与「现在长什么样」对不上。
    要改就发新版本。
    """

    code = errors.code(409, 2)
    message = "该封装模板已发布，不能再修改内容"
    http_status = 409
