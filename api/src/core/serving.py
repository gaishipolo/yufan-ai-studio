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

## 为什么远端必须重定向而不是中转

产物可能是几十上百 MB，中转一次就是「应用内存 × 并发数」；而对象存储自己
会处理 `Range`，视频能直接拖进度条。

**这个模块的位置曾经是 `modules/asset/serving.py`。** 第二个需要下载的模块
（导出）出现时，才发现「要么违规 import 别人的模块，要么抄一份」——
按分层，横切能力属于核心，业务模块只往里加，不往里藏。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import status
from fastapi.responses import FileResponse, RedirectResponse, Response

from core.exceptions import NotFound

__all__ = ["Served"]


@dataclass(frozen=True, slots=True)
class Served:
    """一件可下载的产物。`path` 与 `url` 二选一，都为空就是字节不存在。"""

    path: Path | None = None
    url: str | None = None
    mime: str = "application/octet-stream"
    #: 只对本地驱动生效（`Content-Disposition`）；远端驱动下由对象存储自己带
    #: `response-content-disposition`，不要去改签名 URL 的查询串。
    filename: str | None = None

    def response(self) -> Response:
        if self.path is not None:
            if not self.path.is_file():
                # 「库里有记录、盘上文件没了」是必然会发生的：GC 误删、迁移中断、
                # 运维手工清理、备份还原只还原了数据库。不检查的话 `FileResponse`
                # 在发送前 stat 失败抛 RuntimeError，用户拿到 500「服务器出错」——
                # 而这其实只是一次正常的「产物不在了」，该 404 该重试。
                raise NotFound("产物文件已不存在，请重新生成或重新登记")
            # FileResponse 支持 Range：本地驱动下拖进度条不需要把文件读进内存
            return FileResponse(self.path, media_type=self.mime, filename=self.filename)
        if self.url is None:
            raise NotFound("产物字节不存在，请重新生成")
        return RedirectResponse(self.url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @classmethod
    def local(cls, path: Path, mime: str, *, filename: str | None = None) -> Served:
        return cls(path=path, mime=mime, filename=filename)

    @classmethod
    def remote(cls, url: str, mime: str) -> Served:
        return cls(url=url, mime=mime)
