"""取件出参：本地给文件，远端给 307。

`AssetService`（对象存储）与 `ArchiveAssetService`（归档盘）现在返回同一种形状
`(path, url, mime)`：本地类驱动交路径、远端驱动交预签名地址，二者不会同时非空。
两个路由模块各抄一份 `if path … else RedirectResponse` 只会让「改一处忘一处」
再发生一次，所以出参收敛在这里。

为什么远端必须重定向而不是中转：素材可能是几十上百 MB，中转一次就是
「应用内存 × 并发数」；而对象存储自己会处理 `Range`，视频能直接拖进度条。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import status
from fastapi.responses import FileResponse, RedirectResponse, Response

from core.exceptions import NotFound


def serve(path: Path | None, url: str | None, mime: str) -> Response:
    if path is not None:
        # FileResponse 支持 Range：本地驱动下拖进度条不需要把文件读进内存
        return FileResponse(path, media_type=mime)
    if url is None:  # pragma: no cover - service 已保证二者必有其一
        raise NotFound("素材字节不存在，请重新登记")
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
