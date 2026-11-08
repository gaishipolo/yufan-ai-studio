"""取件出参：本地给文件，远端给 307。

任何「把存储里的字节交给浏览器」的接口都长同一个形状：

    path = storage.local_path(key)            # 本地驱动：有真实路径
    url = await storage.presigned_download_url(key)  # 远端驱动：预签名地址

两者不会同时非空。每个模块各写一遍 `if path … else RedirectResponse`，
只会让「改一处忘一处」再发生一次，所以收敛在这里。

为什么远端必须重定向而不是中转：产物可能是几十上百 MB，中转一次就是
「应用内存 × 并发数」；而对象存储自己会处理 `Range`，视频能直接拖进度条。

**这个模块的位置曾经是 `modules/asset/serving.py`。** 第二个需要下载的模块
（导出）出现时，才发现「要么违规 import 别人的模块，要么抄一份」——
按第 0 节的分层，横切能力属于核心，业务模块只往里加，不往里藏。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import status
from fastapi.responses import FileResponse, RedirectResponse, Response

from core.exceptions import NotFound


def serve(
    path: Path | None,
    url: str | None,
    mime: str,
    *,
    filename: str | None = None,
) -> Response:
    """把「本地路径 / 预签名 URL」二选一的结果变成响应。

    `filename` 只对本地驱动生效（`Content-Disposition`）；远端驱动下由对象存储
    自己带 `response-content-disposition`，调用方不要去改签名 URL 的查询串。
    """
    if path is not None:
        # FileResponse 支持 Range：本地驱动下拖进度条不需要把文件读进内存
        return FileResponse(path, media_type=mime, filename=filename)
    if url is None:  # pragma: no cover - 调用方已保证二者必有其一
        raise NotFound("产物字节不存在，请重新生成")
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
