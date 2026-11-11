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
    # 文本资产（第 5 种文件型资产）。只收纯文本，不收 .html / .svg：
    # 那两种会被浏览器当标记语言执行，与「正文一律纯文本渲染」的口径冲突。
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}
TEXT_EXTENSIONS = frozenset({".txt", ".md"})
MAX_BYTES = 300 * 1024 * 1024
#: 文本单独一档：提示词素材不会是 300MB，但会被整份读进内存做注入。
MAX_TEXT_BYTES = 1024 * 1024
#: 列表卡片上的摘要长度（字符数，不是字节）。
TEXT_EXCERPT_LIMIT = 160
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


def decode_text(data: bytes) -> str:
    """严格 UTF-8 解码，剥掉开头 BOM；失败一律 400 而不是静默替换字符。

    `errors="replace"` 会把一段坏字节变成 U+FFFD，用户拿到一条「看起来能读」
    的提示词资产，提交给供应商时却少了半句话。BOM 也要剥：它落进正文会变成
    提示词开头一个不可见字符，且同一份内容带不带 BOM 会算出两个 sha256，
    秒传与版本去重都会失效。
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadRequest("文本文件必须是 UTF-8 编码（不支持 GBK 等其它编码）") from exc


def encode_text(text: str) -> bytes:
    """规范化后的落盘字节：无 BOM、LF 不动、严格 UTF-8。"""
    return text.encode("utf-8")


def text_excerpt(text: str) -> str:
    """列表卡片摘要：压掉换行与多余空白，避免一段 Markdown 把卡片撑成十行。"""
    return " ".join(text.split())[:TEXT_EXCERPT_LIMIT]


def normalize_text_file(path: Path) -> str:
    """校验并**原地规范化**上传的临时文件，返回解码后的正文。

    重写这一步不能省：内容寻址的 key 是规范化后字节的 sha256。若只解码、
    不回写，同一段文字带 BOM / 不带 BOM 会存成两份不同的字节，编辑器读到的
    正文与 `sha256` 也对不上。文本上限只有 1MB，整份读写是安全的。
    """
    data = path.read_bytes()
    if len(data) > MAX_TEXT_BYTES:
        raise BadRequest("文本文件不能超过 1MB")
    text = decode_text(data)
    if not text.strip():
        raise BadRequest("文本内容不能为空")
    if len(encode_text(text)) > MAX_TEXT_BYTES:
        raise BadRequest("文本文件不能超过 1MB")
    path.write_bytes(encode_text(text))
    return text


def inspect(path: Path, extension: str) -> MediaInfo:
    if extension not in EXTENSIONS:
        raise BadRequest("支持 MP4、PNG、JPG、WebP、MP3、WAV、TXT 和 MD")
    if extension in TEXT_EXTENSIONS:
        normalize_text_file(path)
        return {"width": 0, "height": 0, "duration": 0, "hasAudio": False}
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
    probe = _probe_stream(path)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
    if not duration:
        raise BadRequest("音视频文件无法解析")
    seconds = normalize_duration(
        int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    )
    if extension == ".mp4":
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[4:8] != b"ftyp" or "Video:" not in probe:
            raise BadRequest("请使用有效的 MP4 视频")
    elif "Audio:" not in probe:
        raise BadRequest("请使用有效的音频文件")
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
    # 文本没有缩略图：卡片按 `textExcerpt` 画文字卡。`cover_key` 恒为空，
    # 前端必须按 `previewUrl === null` 跳过 `/thumbnail`，否则每张卡一次 404。
    if kind in {"audio", "text"}:
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
