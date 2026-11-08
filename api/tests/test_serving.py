"""`Served` 的出参形态：本地给文件、远端给 307、都不给就 404。

这个模块此前**没有任何测试**，而它是所有「把字节交给浏览器」的接口的必经之路
（素材原件、缩略图、导出包、成片）。它的三种出参里，两种的错误分支都是
「用户可见的失败」：

- 本地文件被清掉（GC 误删 / 迁移中断 / 备份只还原了库）→ 曾经是 **500**
  `Internal Server Error`，纯文本、无错误码，前端只能弹「服务器出错」；
- 没有路径也没有地址 → `NotFound`；
- 远端分支必须是 **307**，302 会把 `Range` 语义改掉（视频拖不动进度条）。

`FileResponse` / `RedirectResponse` 是 Starlette 的类型，这里只验证「选了哪个
分支、带了哪些头」，不重复测 Starlette 自己的字节搬运。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import status
from fastapi.responses import FileResponse, RedirectResponse

from core.exceptions import NotFound
from core.serving import Served


def test_local_file_becomes_file_response(tmp_path: Path) -> None:
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"0123456789")

    response = Served.local(target, "video/mp4", filename="clip.mp4").response()

    assert isinstance(response, FileResponse)
    assert response.media_type == "video/mp4"
    # 只在本地产物上生效：远端由对象存储自己带 response-content-disposition
    assert response.headers["content-disposition"] == 'attachment; filename="clip.mp4"'
    # Range 支持是本地驱动相对远端唯一的优势：视频能直接拖进度条
    assert response.headers["accept-ranges"] == "bytes"


def test_non_ascii_filename_uses_rfc5987(tmp_path: Path) -> None:
    """中文下载名必须走 `filename*=utf-8''…`：直接塞进 `filename=` 会被客户端
    按 latin-1 解成乱码，或被 Starlette 编码成 URL 转义后原样显示给用户。
    """
    target = tmp_path / "成片.mp4"
    target.write_bytes(b"x")

    response = Served.local(target, "video/mp4", filename="成片.mp4").response()

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=utf-8''")
    assert "%E6%88%90%E7%89%87.mp4" in disposition


def test_missing_local_file_is_not_found_not_server_error(tmp_path: Path) -> None:
    """盘上文件没了是「产物不在了」，不是「服务器坏了」。

    这条曾经是 500：`FileResponse` 在发送前 stat 失败抛 RuntimeError，被兜底
    处理器收成 `Internal Server Error`，连错误码都没有。
    """
    with pytest.raises(NotFound):
        Served.local(tmp_path / "gone.mp4", "video/mp4").response()


def test_directory_is_not_served_as_a_file(tmp_path: Path) -> None:
    """目录不是产物：`is_file()` 而非 `exists()`，否则会尝试把目录当文件发。"""
    folder = tmp_path / "artifacts"
    folder.mkdir()

    with pytest.raises(NotFound):
        Served.local(folder, "application/octet-stream").response()


def test_remote_url_redirects_with_307() -> None:
    """307 保留方法与 Range；302 会让部分客户端改用 GET 并丢 Range 头。"""
    response = Served.remote("https://bucket.example/x.mp4?sig=1", "video/mp4").response()

    assert isinstance(response, RedirectResponse)
    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == "https://bucket.example/x.mp4?sig=1"


def test_nothing_to_serve_is_not_found() -> None:
    with pytest.raises(NotFound):
        Served().response()


def test_local_wins_over_url_when_both_present(tmp_path: Path) -> None:
    """两者都给的调用是写法错误，但必须有确定答案：能直出就不绕一圈签名地址。"""
    target = tmp_path / "a.png"
    target.write_bytes(b"png")

    response = Served(path=target, url="https://bucket.example/a.png", mime="image/png").response()

    assert isinstance(response, FileResponse)
