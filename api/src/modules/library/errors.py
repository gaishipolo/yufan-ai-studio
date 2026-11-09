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


class LibraryFileTypeNotAllowed(AppError):
    """扩展名不在白名单，或与本次上传声明的类型不符。

    只认扩展名 + 内容探测，不认前端传的 `Content-Type`：后者由客户端随意填写，
    把校验建在它上面等于没校验。
    """

    code = errors.code(400, 2)
    message = "支持 MP4、PNG、JPG、WebP、MP3 和 WAV"
    http_status = 400


class LibraryFileTooLarge(AppError):
    """超出单文件上限（300MB，与归档原片同档）。

    `details` 带 `maxBytes`，前端可以显示「最多 300MB」而不是猜。
    """

    code = errors.code(413, 1)
    message = "文件不能超过 300MB"
    http_status = 413


class ShareTargetInvalid(AppError):
    """共享 / 撤销的目标与资产当前状态对不上。

    三种情形共用这个码，因为前端的处理完全一样（刷新详情、重选目标）：

    - 共享到小组，但我不在那个组（管理员也不能替别人往组里塞素材）；
    - 撤销某个组的共享，但资产并没有共享给这个组；
    - 撤销团队共享，但资产根本不在团队库。

    不拆成三个码：拆开只会在前端多出三条一样的提示分支。
    """

    code = errors.code(409, 7)
    message = "共享目标与该资产的当前状态不符"
    http_status = 409


class RequestNotPending(AppError):
    """审批 / 撤回一张已经处理过的申请单。

    并发下两个人同时点「通过」时，第二个会撞在 `expected_version` 或这里。
    两者都给 409，但文案不同：这里说「已被处理」，版本冲突说「已被修改」。
    """

    code = errors.code(409, 8)
    message = "该申请已被处理，请刷新列表"
    http_status = 409


class DownloadNotReady(AppError):
    """打包还没完成（或已失败 / 已过期）就取文件。

    占掉 409 的最后一个槽位。再要新语义就得按 `EXTENDING.md` §7.1 的预案
    把编码扩到 6 位——所以**不要**把「打包失败」也塞成同码的新文案，
    失败原因在任务的 `error` 字段里，前端读它。
    """

    code = errors.code(409, 9)
    message = "打包任务尚未完成或已失效"
    http_status = 409


class PackItemInvalid(AppError):
    """封装成员不合法：看不见 / 不在库 / 是另一个封装 / 把自己装进去。

    四种情形共用一个码：前端能做的都是同一件事（刷新选择器、去掉那几项）。
    `details.items` 带具体不合法的是哪几个，别让用户猜。
    """

    code = errors.code(400, 3)
    message = "封装里有不可用的成员"
    http_status = 400


class PackEmpty(AppError):
    """封装一个成员都没有。空封装没有意义：它没有字节、下载也解不出东西。"""

    code = errors.code(400, 4)
    message = "封装至少需要一个成员"
    http_status = 400


class GenerationSourceInvalid(AppError):
    """生成入库没给能物化的来源，或来源取不到字节。

    **不接受「只存外部 URL」**：供应商签名地址会过期，详情页与下载都会裂。
    所以入库这一侧必须是「下载下来存进我们自己的存储」。
    """

    code = errors.code(400, 5)
    message = "生成结果必须提供可读取的 storageKey 或 sourceUrl"
    http_status = 400


class DownloadEmpty(AppError):
    """选中的条目一个都不能下载（全是草稿 / 回收站 / 看不见的）。"""

    code = errors.code(400, 6)
    message = "选中的条目都不在可下载范围内"
    http_status = 400


class DownloadTooLarge(AppError):
    """打包超出上限（100 个条目 / 解压后 2GB）。

    `details` 带 `limit` 与 `actual`，前端据此提示「分两批」而不是只说「太大了」。
    """

    code = errors.code(413, 2)
    message = "单次打包超出上限"
    http_status = 413


__all__ = [
    "AssetNotInTrash",
    "DownloadEmpty",
    "DownloadNotReady",
    "DownloadTooLarge",
    "DraftNotReviewable",
    "DraftReviewForbidden",
    "GenerationSourceInvalid",
    "LibraryFileTooLarge",
    "LibraryFileTypeNotAllowed",
    "PackEmpty",
    "PackItemInvalid",
    "RequestNotPending",
    "RevokeReasonRequired",
    "ShareRevokePending",
    "ShareTargetInvalid",
    "SharedAssetNotDeletable",
    "TeamDeleteForbidden",
    "TrashEntryReferenced",
    "errors",
]
