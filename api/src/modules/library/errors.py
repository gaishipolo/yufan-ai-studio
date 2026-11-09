"""资产库 v2 错误码（slot 8，见 `api/docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + 8 × 10 + seq`，因此 `code // 100` 恒等于 HTTP 状态码。
每个 (状态码, seq) 只能用一次，`ModuleErrors` 在导入期检查。

slot 8 原先登记为「notify / billing（尚未落地）」，资产库 v2 先占用；两者若将来
同时落地，按 §7 的规则共用号段、靠 `seq` 区分具体码值，不新增编码位数。

与旧 `modules/asset`（slot 5）完全隔离：并存期两套库同时在在线，错误码不能撞，
否则前端只能按 message 猜是哪一边失败的。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("library", slot=8)


class SharedAssetNotDeletable(AppError):
    """资产仍共享到小组或团队，不能直接删除。

    `details` 里带 `canRevokeAndDelete`：命中「本人所有 + 只共享给本组 + 我是组长」
    的窄口时为 `true`，前端据此把按钮换成「撤销共享并删除」（见设计 §3.4.1）。
    其余情况为 `false`，用户需要先走撤销审批。
    """

    code = errors.code(409, 1)
    message = "该资产仍处于共享状态，请先撤销共享后再删除"
    http_status = 409


class TeamDeleteForbidden(AppError):
    """团队库资产一律不可删除，包括上传者本人。

    「团队」是全员可见范围，删掉会影响所有人，所以收紧到「只能先撤销团队共享」。
    与 `SharedAssetNotDeletable` 分开：这个连窄口都不适用，提示文案也不同。
    """

    code = errors.code(409, 2)
    message = "团队库资产不能直接删除，请先撤销团队共享"
    http_status = 409


class ShareRevokePending(AppError):
    """撤销共享的审批还没通过，删除请求被拒。"""

    code = errors.code(409, 3)
    message = "撤销共享正在审批中，通过后再删除"
    http_status = 409


class DraftNotReviewable(AppError):
    """草稿已被处理（并发重复采纳 / 驳回），或不在草稿状态。"""

    code = errors.code(409, 4)
    message = "该草稿已被处理，请刷新后重试"
    http_status = 409


class AssetNotInTrash(AppError):
    """对不在回收站的条目调用 restore / purge。"""

    code = errors.code(409, 5)
    message = "该资产不在回收站"
    http_status = 409


class TrashEntryReferenced(AppError):
    """彻底删除前发现仍被封装 / 下载任务 / 跨模块引用。

    `details` 里带引用方描述：只说「被引用」不说被谁引用，用户只能去猜。
    """

    code = errors.code(409, 6)
    message = "资产仍被引用，不能彻底删除"
    http_status = 409


class RevokeReasonRequired(AppError):
    """发起撤销共享（`action=revoke`）却没给理由。"""

    code = errors.code(400, 1)
    message = "撤销共享必须填写理由"
    http_status = 400


class DraftReviewForbidden(AppError):
    """审核他人草稿但没有 `library:draft:review`。

    本人审自己的草稿不需要这个码（持 `library:create` 即可），所以它是 403 不是 404：
    草稿本身对审核人是可见的，缺的是动作权限。
    """

    code = errors.code(403, 1)
    message = "没有审核他人草稿的权限"
    http_status = 403


__all__ = [
    "AssetNotInTrash",
    "DraftNotReviewable",
    "DraftReviewForbidden",
    "RevokeReasonRequired",
    "ShareRevokePending",
    "SharedAssetNotDeletable",
    "TeamDeleteForbidden",
    "TrashEntryReferenced",
    "errors",
]
