"""取件出参：本地给文件，远端给 307。

任何「把存储里的字节交给浏览器」的接口都长同一个形状：拿到一个本地路径
或者一个预签名地址，二选一，然后变成一个响应。

## 为什么要有 `Served` 而不只是一个函数

`serve(path, url, mime)` 的签名让每个调用方都要：

1. 自己算出 `path` 和 `url` 两个值（哪怕只可能有一个非空）；
2. 按位置参数传，顺序错了类型检查不一定报（两个都是 `str | None` 时直接静默出错）；
3. 各自定义承载它的类型——本仓库曾经同时存在 `asset` 的
   `Serve = tuple[Path | None, str | None, str]` 和 `export` 的 `DownloadRef`
   dataclass，两个结构编码的是同一件事。

`Served` 把「一件可下载的产物」变成一个有名字的类型，`response()` 把
「怎么交出去」收在一处。

## 边界：取数留在业务侧

`Served` 只管**成响应**，不管**去哪取**。素材走归档盘的 `BlobStore`
（要 `materialize` 才能拿到稳定路径），导出走对象存储的 `local_path` /
预签名——这是两条不同的存储通路，把它们强行收进一个「storage 取件接口」
会让框架认识归档盘的 CAS 细节，方向是反的。

与之配套的业务规则（远端分支必须先 HEAD，否则 307 到一个 403 的桶地址）
也留在各自 service：那是「取数是否成功」的判断，不是「怎么交出去」。

## 为什么远端默认重定向而不是中转

产物可能是几十上百 MB，中转一次就是「应用内存 × 并发数」；而对象存储自己
会处理 `Range`，视频能直接拖进度条。

**但有一条例外：交给 `XHR` 取 blob 的下载。** 前端拿 zip 是先 `fetch` 成 blob
再 `saveBlob` 落到磁盘的（要自己控制文件名与失败提示），而 `307` 到对象存储
等于让浏览器发起一个**跨域**请求——目标必须回 CORS 头，否则控制台报
「blocked by CORS policy」。MinIO 的桶级 `PutBucketCors` 在当前版本回
NotImplemented，这条路只能靠环境配置兜，换个部署就复发。

所以给「下载产物」类接口留了 `Served.stream()`：同源转发、不依赖任何桶配置，
`Content-Length` 与文件名都由我们自己给。它只在**下载**上用；图片 / 视频
预览仍然走 `307`（`<img>` / `<video>` 不受 CORS 限制，且 Range 由桶处理）。

**这个模块的位置曾经是 `modules/asset/serving.py`。** 第二个需要下载的模块
（导出）出现时，才发现「要么违规 import 别人的模块，要么抄一份」——
按分层，横切能力属于核心，业务模块只往里加，不往里藏。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import status
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from core.exceptions import NotFound

__all__ = ["Served"]


@dataclass(frozen=True, slots=True)
class Served:
    """一件可下载的产物。`path` / `url` / `chunks` 三选一，都为空就是字节不存在。"""

    path: Path | None = None
    url: str | None = None
    mime: str = "application/octet-stream"
    #: 只对本地驱动生效（`Content-Disposition`）；远端驱动下由对象存储自己带
    #: `response-content-disposition`，不要去改签名 URL 的查询串。
    filename: str | None = None
    #: 同源转发用的字节流（见模块 docstring 里「例外」那段）
    chunks: AsyncIterator[bytes] | None = None
    #: 转发时的 `Content-Length`。不知道就不设，让响应退化成 chunked。
    size: int | None = None
    #: 额外的响应头。文本取件用它带 `X-Content-Type-Options: nosniff`——
    #: 只声明 `text/plain` 不够，浏览器仍可能嗅探后把 `.md` 里的片段当 HTML 执行。
    headers: Mapping[str, str] | None = None

    def response(self) -> Response:
        extra = dict(self.headers or {})
        if self.path is not None:
            if not self.path.is_file():
                # 「库里有记录、盘上文件没了」是必然会发生的：GC 误删、迁移中断、
                # 运维手工清理、备份还原只还原了数据库。不检查的话 `FileResponse`
                # 在发送前 stat 失败抛 RuntimeError，用户拿到 500「服务器出错」——
                # 而这其实只是一次正常的「产物不在了」，该 404 该重试。
                raise NotFound("产物文件已不存在，请重新生成或重新登记")
            # FileResponse 支持 Range：本地驱动下拖进度条不需要把文件读进内存
            return FileResponse(
                self.path,
                media_type=self.mime,
                filename=self.filename,
                headers=extra or None,
            )
        if self.chunks is not None:
            headers = extra
            if self.size is not None:
                # 有长度浏览器才显示进度百分比；没有就是一根不确定进度的转圈
                headers["Content-Length"] = str(self.size)
            if self.filename:
                # 与 `FileResponse` 的写法保持一致（RFC 5987），中文名不会被截断
                headers["Content-Disposition"] = (
                    f"attachment; filename*=utf-8''{quote(self.filename)}"
                )
            return StreamingResponse(
                self.chunks, media_type=self.mime, headers=headers or None
            )
        if self.url is None:
            raise NotFound("产物字节不存在，请重新生成")
        return RedirectResponse(
            self.url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers=extra or None,
        )

    @classmethod
    def local(
        cls,
        path: Path,
        mime: str,
        *,
        filename: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Served:
        return cls(path=path, mime=mime, filename=filename, headers=headers)

    @classmethod
    def remote(
        cls, url: str, mime: str, *, headers: Mapping[str, str] | None = None
    ) -> Served:
        return cls(url=url, mime=mime, headers=headers)

    @classmethod
    def stream(
        cls,
        chunks: AsyncIterator[bytes],
        mime: str,
        *,
        filename: str | None = None,
        size: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Served:
        """同源转发。只在「前端会用 XHR 取、且目标存储不保证 CORS」时用。"""
        return cls(
            chunks=chunks,
            mime=mime,
            filename=filename,
            size=size,
            headers=headers,
        )
