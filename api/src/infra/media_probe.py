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


def inspect(path: Path, extension: str) -> MediaInfo:
    if extension not in EXTENSIONS:
        raise BadRequest("支持 MP4、PNG、JPG、WebP、MP3 和 WAV")
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise BadRequest("文件不能为空，且不能超过 300MB")
    if EXTENSIONS[extension].startswith("image/"):
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
                if image.format != expected[extension]:
                    raise BadRequest("图片内容与扩展名不一致")
                return {
                    "width": image.width,
                    "height": image.height,
                    "duration": 0,
                    "hasAudio": False,
                }
        except (OSError, Image.DecompressionBombError) as exc:
            raise BadRequest("图片无法解析") from exc
    probe = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe.stderr)
    if not duration:
        raise BadRequest("音视频文件无法解析")
    seconds = normalize_duration(
        int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    )
    if extension == ".mp4":
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[4:8] != b"ftyp" or "Video:" not in probe.stderr:
            raise BadRequest("请使用有效的 MP4 视频")
    elif "Audio:" not in probe.stderr:
        raise BadRequest("请使用有效的音频文件")
    size = re.search(r"Video:.*? (\d{2,5})x(\d{2,5})", probe.stderr)
    return {
        "width": int(size[1]) if size else 0,
        "height": int(size[2]) if size else 0,
        "duration": seconds,
        "hasAudio": "Audio:" in probe.stderr,
    }


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
            "scale=320:-2",
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
    <video> 无法解码；统一转成 H.264 8-bit + AAC、moov 前置的 360p 小文件，
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
                "scale=-2:360",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-profile:v",
                "main",
                "-crf",
                "28",
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
