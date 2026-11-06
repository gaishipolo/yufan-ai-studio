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


class AssetInUse(AppError):
    """仍被模板/分镜等引用。

    `details` 里带引用方描述：只说「被引用」而不说被谁引用，用户只能去猜。
    """

    code = errors.code(409, 1)
    message = "素材仍被引用，不能删除"
    http_status = 409


__all__ = [
    "AssetInUse",
    "AssetNotFound",
    "FileTooLarge",
    "FileTypeNotAllowed",
    "errors",
]
