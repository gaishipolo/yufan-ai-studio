"""归档视频的时长归一化与浏览器兼容预览；只用临时目录，不访问 Z 盘。

素材登记一律走 HTTP 上传：索引在 `assets` 表，服务层构造（`ArchiveAssetService()`）
需要 session，测试里直接调同步方法等于绕开真实的入库路径。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests_archive_helpers import archive

from infra.archive import clip_key, preview_key
from infra.archive.local import get_archive_settings
from infra.media_probe import create_browser_preview, inspect, normalize_duration


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # 归档根是驱动私有配置（前缀 MEDIA_ARCHIVE_），不在全局 settings 里
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def make_video(path: Path, size: str = "64x64") -> Path:
    # 颜色取随机值：内容摘要参与归档去重，两个用例生成同一份字节会互相命中
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{os.urandom(3).hex()}:s={size}:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_audio_tail_padding_does_not_extend_source_duration() -> None:
    # AAC 帧填充让 15 秒原片报 15.02 / 15.07 秒，不应被 1–15 秒门禁误判。
    assert normalize_duration(15.07) == 15.0
    assert normalize_duration(15.02) == 15.0
    assert normalize_duration(8.01) == 8.0
    # 真实超长内容仍大于 15 秒，业务侧照常拒绝；非整秒内容保留小数。
    assert normalize_duration(15.9) == 15.9
    assert normalize_duration(16.01) == 16.0
    assert normalize_duration(3.5) == 3.5


def test_stream_serves_playable_derivative_and_preserves_original(
    client: TestClient, archive_root: Path
) -> None:
    source = make_video(archive_root / "sample.mp4")
    original = source.read_bytes()
    media = archive.upload(client, "sample.mp4", "source", original)
    response = client.get(f"/api/v1/assets/archive/{media['id']}/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    # 派生流按素材摘要命名（`previews/ab/abcdef…hd-v2.mp4`）：同一原片只派生一次，
    # 版本号仍在 key 里，升级转码参数时旧缓存自动失效
    derived = archive_root / clip_key(media["sha256"])
    assert derived.is_file()
    assert derived.read_bytes() == response.content
    assert source.read_bytes() == original  # 派生预览绝不覆盖原片
    probe = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(derived)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Video: h264" in probe.stderr
    assert "yuv420p(" in probe.stderr  # 8-bit，浏览器可解码
    assert "yuv420p10" not in probe.stderr
    assert client.get(media["url"]).content == original
    assert inspect(derived, ".mp4")["width"] == 64  # 不放大小尺寸原片


def test_portrait_thumbnail_keeps_detail(client: TestClient, archive_root: Path) -> None:
    """竖屏原片的缩略图不能统一压成固定小图：720×1280 原样保留。"""
    source = make_video(archive_root / "portrait.mp4", "720x1280")
    media = archive.upload(client, "portrait.mp4", "source", source.read_bytes())
    response = client.get(media["previewUrl"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    thumbnail = archive_root / preview_key(media["sha256"])
    assert thumbnail.read_bytes() == response.content
    with Image.open(thumbnail) as image:
        assert image.size == (720, 1280)
    assert client.get(f"/api/v1/assets/archive/{media['id']}/stream").status_code == 200
    info = inspect(archive_root / clip_key(media["sha256"]), ".mp4")
    assert (info["width"], info["height"]) == (720, 1280)


def test_stream_rejects_non_video(client: TestClient, archive_root: Path) -> None:
    image = archive_root / "reference.png"
    Image.new("RGB", (16, 16), tuple(os.urandom(3))).save(image, format="PNG")
    media = archive.upload(client, "reference.png", "character", image.read_bytes())
    assert client.get(f"/api/v1/assets/archive/{media['id']}/stream").status_code == 404


def test_browser_preview_fails_closed_on_bad_input(archive_root: Path) -> None:
    corrupted = archive_root / "broken.mp4"
    corrupted.write_bytes(b"not a video")
    assert not create_browser_preview(corrupted, archive_root / "previews/broken.mp4")
