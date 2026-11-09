"""媒体字节校验与元数据探测，不处理业务归属。"""

from __future__ import annotations

import math
import re
import subprocess
import uuid
from pathlib import Path
from typing import TypedDict

import imageio_ffmpeg
from PIL import Image

from core.exceptions import BadRequest

EXTENSIONS = {
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}
MAX_BYTES = 300 * 1024 * 1024
# 探测单次上限。超过它就不是「这个文件有点慢」，而是解析不出来。
PROBE_TIMEOUT_SEC = 30.0
PREVIEW_VERSION = "hd-v2"
# AAC 每帧 1024 采样（48kHz 下 21.3ms），音轨尾部填充会让容器时长比画面多几十毫秒。
# 15 秒原片因此常报 15.02 / 15.07 秒，被 1–15 秒门禁误判为超长素材。
TAIL_PADDING = 0.1


def normalize_duration(seconds: float) -> float:
    """把容器时长的音轨尾部填充归整到整秒，画面时长保持不变。

    只处理不足一帧量级的尾数；15.9 秒这类真实超长素材仍原样保留，
    由业务门禁拒绝，不会把内容静默截断成 15 秒。
    """
    whole = math.floor(seconds)
    return float(whole) if seconds - whole <= TAIL_PADDING else round(seconds, 2)


class MediaInfo(TypedDict):
    width: int
    height: int
    duration: float
    hasAudio: bool


#: 扩展名 → 图片格式名。只用于「按扩展名校验」那条路径（`kind=None`）。
_IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


def inspect(path: Path, extension: str, *, kind: str | None = None) -> MediaInfo:
    """读元数据并校验字节，`extension` 决定走图片还是音视频分支。

    `kind` 给定时按**内容判定的类型**分派，不再用扩展名选分支。这是 M1-E 的
    配套：文件被改名后扩展名与内容冲突，元数据仍然要读得出来——否则一张改名
    成 `.mp4` 的 PNG 会在 ffmpeg 那里报「音视频文件无法解析」，而用户以为
    视频坏了，实际只是文件名写错了。

    `kind=None` 保留旧行为（严格按扩展名分派并校验格式一致），供调用方在
    内容判不出来时兜底。
    """
    if extension not in EXTENSIONS:
        raise BadRequest("支持 MP4、PNG、JPG、WebP、MP3 和 WAV")
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise BadRequest("文件不能为空，且不能超过 300MB")
    # 内容判定优先：`kind` 说了算时扩展名只用于「无可信类型」的格式一致性校验
    trust_extension = kind is None
    branch = kind or ("image" if EXTENSIONS[extension].startswith("image/") else "stream")
    if branch == "image":
        return _inspect_image(path, extension if trust_extension else None)
    return _inspect_stream(path, extension if trust_extension else None)


def _inspect_image(path: Path, expected_extension: str | None) -> MediaInfo:
    """图片分支。`expected_extension` 为空表示格式以内容为准，不比对外声称的格式。"""
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if expected_extension and image.format != _IMAGE_FORMATS[expected_extension]:
                raise BadRequest("图片内容与扩展名不一致")
            return {
                "width": image.width,
                "height": image.height,
                "duration": 0,
                "hasAudio": False,
            }
    except (OSError, Image.DecompressionBombError) as exc:
        raise BadRequest("图片无法解析") from exc


def _inspect_stream(path: Path, expected_extension: str | None) -> MediaInfo:
    """音视频分支。`expected_extension` 为空时只要求「能读出时长」，
    不再追加「必须含视频流 / 必须含音频流」——那两条是按扩展名推断期望值才成立的
    判断，内容已经自己说明类型时再套一遍只会误伤。"""
    probe = _probe_stream(path)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
    if not duration:
        raise BadRequest("音视频文件无法解析")
    if expected_extension == ".mp4":
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[4:8] != b"ftyp" or "Video:" not in probe:
            raise BadRequest("请使用有效的 MP4 视频")
    elif expected_extension is not None and "Audio:" not in probe:
        raise BadRequest("请使用有效的音频文件")
    seconds = normalize_duration(
        int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    )
    size = re.search(r"Video:.*? (\d{2,5})x(\d{2,5})", probe)
    return {
        "width": int(size[1]) if size else 0,
        "height": int(size[2]) if size else 0,
        "duration": seconds,
        "hasAudio": "Audio:" in probe,
    }


def _probe_stream(path: Path) -> str:
    """跑 `ffmpeg -i` 取 stderr 里的容器信息。

    超时必须是 `BadRequest` 而不是让 `TimeoutExpired` 冒出去：300MB 的畸形文件、
    停在共享盘上的半成品都可能让它跑满 30 秒。那不是服务端故障，用户该看到的是
    「请换一个文件」，而不是 500「服务器内部错误」——后者只会让他来问我们。
    """
    try:
        probe = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise BadRequest(
            f"音视频文件解析超时（{PROBE_TIMEOUT_SEC:.0f} 秒），请检查文件是否损坏"
        ) from exc
    return probe.stderr


def create_preview(source: Path, destination: Path, kind: str) -> bool:
    if kind == "audio":
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return True
    if kind == "image":
        with Image.open(source) as image:
            image.thumbnail((360, 240))
            image.convert("RGB").save(destination, format="JPEG", quality=82)
        return True
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=w='min(720,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease",
            "-q:v",
            "2",
            "-update",
            "1",
            str(destination),
        ],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def create_browser_preview(source: Path, destination: Path) -> bool:
    """派生浏览器可解码的轻量预览，绝不改动原始归档文件。

    归档原片可能是 HEVC Main10 或 H.264 High10（10-bit），Chrome/Edge 的
    <video> 无法解码；统一转成 H.264 8-bit + AAC、moov 前置的高清预览，
    同时避免悬停预览下载 10–24MB 原始视频。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.stem}.{uuid.uuid4().hex}.tmp.mp4")
    try:
        result = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                "scale=w='min(1920,iw)':h='min(1920,ih)':"
                "force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-profile:v",
                "main",
                "-crf",
                "20",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                str(temporary),
            ],
            capture_output=True,
            timeout=180,
        )
        if result.returncode != 0 or not temporary.is_file():
            return False
        temporary.replace(destination)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        temporary.unlink(missing_ok=True)
