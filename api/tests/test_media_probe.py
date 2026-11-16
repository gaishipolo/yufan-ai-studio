"""媒体探测的错误分类：文件坏是 400，服务坏才是 500。

`inspect()` 是上传链路的第一道门。它抛 `BadRequest` 时用户看到「请使用有效的
MP4 视频」；抛别的类型就落进兜底分支变成 500「服务器内部错误」——同一件事，
两种说法，一个能自助解决，一个只能来问我们。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.exceptions import BadRequest
from infra import media_probe


def _fake_mp4(path: Path) -> Path:
    """够过「非空 + 不超过 300MB」检查的假文件；内容不重要，探测会被打桩。"""
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    return path


def test_probe_timeout_is_reported_as_a_bad_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg 探测超时是「这个文件我们解析不了」，不是服务端故障。

    300MB 的畸形文件、卡在共享盘上的半成品、需要联网取索引的容器，都可能让
    `ffmpeg -i` 跑满 30 秒。超时抛的是 `subprocess.TimeoutExpired`，它不是
    `BadRequest`，于是用户拿到 500——而真正该说的话是「请换一个文件」。
    """
    source = _fake_mp4(tmp_path / "clip.mp4")

    def slow_probe(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

    monkeypatch.setattr(media_probe.subprocess, "run", slow_probe)

    with pytest.raises(BadRequest, match="超时"):
        media_probe.inspect(source, ".mp4")


def test_unparsable_container_is_a_bad_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照组：真正「解析不出来」也是 400。两条路径的错误码必须一致。"""

    class Result:
        returncode = 1
        stderr = "clip.mp4: Invalid data found when processing input"

    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **k: Result())

    with pytest.raises(BadRequest, match="无法解析"):
        media_probe.inspect(_fake_mp4(tmp_path / "bad.mp4"), ".mp4")


# --------------------------------------------------------------------------- 封面生成
#
# 封面生成失败必须返回 False，而不是把异常抛出去：上传链是「先写 blob、再落库」，
# 抛出去就是 500 + 主存储里一份没人引用的字节。同文件的 create_browser_preview
# 早就按这个口径写了，create_preview 当时漏了。


def test_create_preview_returns_false_when_ffmpeg_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首帧抽取超时是「这个视频解不开」，不是服务端故障。"""
    source = _fake_mp4(tmp_path / "clip.mp4")

    def slow(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

    monkeypatch.setattr(media_probe.subprocess, "run", slow)

    assert media_probe.create_preview(source, tmp_path / "cover.jpg", "video") is False


def test_create_preview_returns_false_when_ffmpeg_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg 起不来（OSError）同样只是「没有封面」。"""
    source = _fake_mp4(tmp_path / "clip.mp4")

    def missing(*args: object, **kwargs: object) -> None:
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(media_probe.subprocess, "run", missing)

    assert media_probe.create_preview(source, tmp_path / "cover.jpg", "video") is False


def test_create_preview_returns_false_for_a_broken_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图片解码失败也只是「这张图坏了」。"""
    source = tmp_path / "broken.png"
    source.write_bytes(b"not-a-png")

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("cannot identify image file")

    monkeypatch.setattr(media_probe.Image, "open", broken)

    assert media_probe.create_preview(source, tmp_path / "cover.jpg", "image") is False


# --------------------------------------------------------------------------- 像素上限


def _png_header_only(width: int, height: int) -> bytes:
    """只有文件头的 PNG：宽高在 IHDR 里，PIL 打开时就能读到，不必真造出像素。"""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_inspect_rejects_an_image_with_too_many_pixels(tmp_path: Path) -> None:
    """超大像素的图片要**在探测阶段**就拒掉。

    PIL 只在超过 2×MAX_IMAGE_PIXELS（约 1.79 亿像素）时才抛 DecompressionBombError，
    而 8000×6000（约 4800 万像素）能通过那个告警区间：真正吃内存的是生成缩略图
    那一步的**完整解码**（4800 万像素 × 4 字节 ≈ 190MB）。
    """
    source = tmp_path / "huge.png"
    source.write_bytes(_png_header_only(8_000, 6_000))

    with pytest.raises(BadRequest, match="像素"):
        media_probe.inspect(source, ".png")


def test_inspect_accepts_an_image_under_the_pixel_limit(tmp_path: Path) -> None:
    """上限之上要拒、上限之下要放行：别把一张正常的图也拦了。"""
    from PIL import Image

    source = tmp_path / "normal.png"
    Image.new("RGB", (64, 64), "teal").save(source, format="PNG")

    info = media_probe.inspect(source, ".png")
    assert (info["width"], info["height"]) == (64, 64)
