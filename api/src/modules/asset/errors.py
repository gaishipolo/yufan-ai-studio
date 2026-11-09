"""素材模块错误码（slot 5，见 `docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + slot × 10 + seq`，因此 `code // 100` 恒等于 HTTP 状态码。
每个 (状态码, seq) 只能用一次，`ModuleErrors` 会在导入期检查。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("asset", slot=5)


class AssetNotFound(AppError):
    """素材不存在，或不属于当前用户。

    两种情况共用同一个错误：区分开等于把「这个 id 存在」白送给非本人。
    """

    code = errors.code(404, 1)
    message = "素材不存在"
    http_status = 404


class FileTypeNotAllowed(AppError):
    """扩展名不在白名单里。

    只认扩展名 + 探测内容，不认前端传来的 Content-Type：`Content-Type` 由客户端
    随意填写，把校验建在它上面等于没校验。
    """

    code = errors.code(400, 1)
    message = "支持 MP4、PNG、JPG、WebP、MP3 和 WAV"
    http_status = 400


class FileTooLarge(AppError):
    """超出单文件上限。

    与归档盘（300MB 原片）分开：素材库是个人创作素材，50MB 覆盖图片与短视频，
    再大的文件引导走归档库，避免随手一个 2GB 原片进库拖慢列表与备份。
    """

    code = errors.code(413, 1)
    message = "文件不能超过 50MB"
    http_status = 413


class AssetQuotaExceeded(AppError):
    """单用户归档容量用尽。

    与 `FileTooLarge`（单个文件太大）分开：后者换个文件就行，前者要先清理
    回收站或提配额。混成一个错误，用户会一直重试上传同一份文件。
    """

    code = errors.code(413, 2)
    message = "归档空间已用完，请先清理回收站或联系管理员提升配额"
    http_status = 413


class AssetInUse(AppError):
    """仍被模板/分镜等引用。

    `details` 里带引用方描述：只说「被引用」而不说被谁引用，用户只能去猜。
    """

    code = errors.code(409, 1)
    message = "素材仍被引用，不能删除"
    http_status = 409


class TagUnknown(AppError):
    """标签 id 不在词表里（或已归档 / 已合并）。

    用 422 而不是 400：这个码只在「接口只接受词表内的 id」这件事被违反时出现
    （见设计文档 §3.3 的落地方式）。前端那个搜索框是词表内模糊匹配，
    收到未知 id 说明调用方自己在拼词——这是程序问题，不是用户输入问题。
    """

    code = errors.code(422, 1)
    message = "标签不在词表内，请从标签列表中选择"
    http_status = 422


class TagExists(AppError):
    """同名词已经存在。

    全局重名会让筛选条出现两个一模一样的分组标题，用户无从选择——
    所以这里不合并、不复用，直接拒绝并提示去用已有的那个词。
    """

    code = errors.code(409, 2)
    message = "已存在同名标签"
    http_status = 409


class ViewNameExists(AppError):
    """同一个人的保存视图重名。

    恢复筛选时无法判断该用哪个，而「两个都恢复一遍再看」不是用户该做的事。
    """

    code = errors.code(409, 3)
    message = "已存在同名视图"
    http_status = 409


class ViewNotFound(AppError):
    """保存视图不存在，或不属于当前用户。两者共用一个错误：区分开等于白送存在性。"""

    code = errors.code(404, 2)
    message = "视图不存在"
    http_status = 404


class NotGenerated(AppError):
    """上传件没有生成配方。

    与 404 分开：素材明明存在、也能下载，只是"没有可提取的提示词"。
    回 404 会让前端以为链接失效；回一句 400 才能指向正确动作——
    要去生成页写提示词，而不是去别处找回它。
    """

    code = errors.code(400, 2)
    message = "这张素材是上传件，没有生成配方可提取"
    http_status = 400


class CandidateClosed(AppError):
    """候选已弃用或已过期，不能采纳。

    过期**不复活**：能恢复的话，7 天前的中间产物会永远留在库里——
    它既不是素材也不是废片，清理任务永远等不到它。要用就重新生成一次。
    """

    code = errors.code(409, 4)
    message = "候选已弃用或已过期，请重新生成"
    http_status = 409


class ComplianceRejected(AppError):
    """合规审核未通过。

    驳回是**终态**（对这条素材而言）：不是"还没审"，所以不能靠等待解决。
    与"待审"分开是必要的——前者要重做，后者只要等。
    """

    code = errors.code(409, 5)
    message = "这张素材未通过合规审核，不能采纳"
    http_status = 409


__all__ = [
    "AssetInUse",
    "AssetNotFound",
    "AssetQuotaExceeded",
    "CandidateClosed",
    "ComplianceRejected",
    "FileTooLarge",
    "FileTypeNotAllowed",
    "NotGenerated",
    "TagExists",
    "TagUnknown",
    "ViewNameExists",
    "ViewNotFound",
    "errors",
]
