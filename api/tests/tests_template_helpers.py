"""素材字节与上传的共享夹具（模板相关用例共用）。

文件名不带 `test_` 前缀，pytest 不会把它当用例收集（与 `tests_archive_helpers`
同一约定）。

**2026-11-14 P3 之后只剩这一半**：原先这里还造「一条替换任务」的现场（`add_run` /
`workspace` / `add_pack` —— 数据库行 + 归档工作区 + 素材包三件套），随着替换链
与 `replacement_runs` / `template_packs` 表下线一起删掉了。现在替换是普通
`POST /tasks`，视频任务现场由 `test_task_api` 那套夹具直接造。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from fastapi.testclient import TestClient
from PIL import Image

API = "/api/v1"

# 默认 owner：演示超管。测试里凡是「谁的数据」不影响结论的，都用它。
ADMIN = "u_001"


# --------------------------------------------------------------------------- #
# 素材字节
# --------------------------------------------------------------------------- #
def png_bytes(size: tuple[int, int] = (32, 48), color: str | None = None) -> bytes:
    """PNG 字节。颜色默认随机：资产库跨用例共享，同字节会被摘要去重命中上一条
    用例的记录，而那条记录的字节在上一轮的临时目录里（已被清掉）。"""
    buffer = __import__("io").BytesIO()
    palette = color or "#" + os.urandom(3).hex()
    Image.new("RGB", size, palette).save(buffer, format="PNG")
    return buffer.getvalue()


def video_bytes(duration: float = 2, *, audio: bool = True) -> bytes:
    """H.264 短视频（默认带 AAC 音轨）。

    要真字节而不是占位：物化与探测会读时长与音轨决定「能不能用」，拿假 MP4 会
    在探测阶段抛异常，测出来的就不是业务规则了。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "clip.mp4"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{os.urandom(3).hex()}:s=64x64:d={duration}",
        ]
        if audio:
            command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        command += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
        command.append(str(target))
        subprocess.run(command, check=True, capture_output=True)
        return target.read_bytes()


#: 旧素材库的分类 → 资产库 v2 的 `kind`。
#: v2 没有「角色/产品/场景」这些业务分类（那由封装时的用途绑定表达），
#: 所以这里的 `category` 只用来决定走哪个上传入口、落成什么 kind。
_UPLOAD_KIND = {
    "source": "video",
    "audio": "audio",
    "text": "text",
    "character": "image",
    "product": "image",
    "scene": "image",
}


def upload(
    client: TestClient,
    filename: str,
    category: str,
    content: bytes,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """走资产库 v2 的真实上传接口登记素材，返回与前端完全一致的出参。

    `category` 保留旧签名，只为让几十条用例不用一起改：v2 只认 `kind`
    （图片 / 音频 / 文本走 `/library/assets/upload`，视频原片走模板库专用的
    `/library/source-videos`）。
    """
    kind = _UPLOAD_KIND.get(category, "image")
    if kind == "video":
        response = client.post(
            f"{API}/library/source-videos",
            params={"filename": filename},
            content=content,
            headers={"Content-Type": "application/octet-stream", **(headers or {})},
        )
    else:
        response = client.post(
            f"{API}/library/assets/upload",
            params={"filename": filename, "kind": kind},
            content=content,
            headers={"Content-Type": "application/octet-stream", **(headers or {})},
        )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def login(
    client: TestClient, username: str = "admin", password: str = "admin@12345"
) -> dict[str, str]:
    response = client.post(
        f"{API}/identity/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["data"]["accessToken"]}


def upload_ids(client: TestClient, kind: str, count: int) -> list[str]:
    """登记 `count` 张同类参考图，返回 id 列表（颜色随机，避免摘要去重）。"""
    return [
        upload(client, f"{kind}-{index}.png", kind, png_bytes())["id"] for index in range(count)
    ]


def actor_id(client: TestClient, headers: dict[str, str] | None = None) -> str:
    response = client.get(f"{API}/identity/me", headers=headers or {})
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["id"])
