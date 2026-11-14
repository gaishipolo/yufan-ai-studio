"""模板库重建后的测试现场：数据库行 + 真实工作区 + 可复用的素材字节。

**为什么要一个共享文件**：重建之后「一条替换任务」的真相分两处——数据库回答
「到哪一步了」，工作区（`run.json` + `inputs/`）回答「文件在哪」，执行器只认
后者。造现场必须两件一起造：只插数据库，详情页会因为 `run_dir()` 找不到
`run.json` 而 404；只造目录，状态机读不到行，`_bump_retry` 之类会 404。
两个都是测试自己的问题，不是代码的问题，所以现场构造集中在这里。

文件名不带 `test_` 前缀，pytest 不会把它当用例收集（与 `tests_archive_helpers`
同一约定）。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete

from db.session import get_session_factory
from infra.archive import get_archive_storage
from infra.archive.runs import relative_key, runs_root, save_pointer
from modules.template.models import (
    ReplacementReview,
    ReplacementRun,
    ReplacementRunInput,
)
from modules.template.workspace import ensure_layout, write_state
from modules.template_pack.models import (
    TemplatePack,
    TemplatePackReference,
    TemplatePackVersion,
)

API = "/api/v1"

# 默认 owner：演示超管。测试里凡是「谁的数据」不影响结论的，都用它，
# 这样不必为每张表造账号，也不会与 session 级 seed 的演示封装抢归属。
ADMIN = "u_001"


# --------------------------------------------------------------------------- #
# 行
# --------------------------------------------------------------------------- #
async def add_run(owner: str = ADMIN, **fields: Any) -> ReplacementRun:
    """插入一条替换任务。`id` 必须是 32 位十六进制，否则 `check_id` 直接 404。"""
    values: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "kind": "character",
        "target": "测试目标",
        "owner_id": owner,
        "status": "prepared",
        "fingerprint": uuid.uuid4().hex,
    }
    values.update(fields)
    run = ReplacementRun(**values)
    async with get_session_factory()() as session:
        session.add(run)
        await session.commit()
    return run


async def read_run(run_id: str) -> ReplacementRun | None:
    async with get_session_factory()() as session:
        return await session.get(ReplacementRun, run_id)


async def patch_run(run_id: str, **fields: Any) -> None:
    """按字段更新一行。比 `read_run` + 改 + commit 短，读的时候少一次往返。"""
    async with get_session_factory()() as session:
        run = await session.get(ReplacementRun, run_id)
        assert run is not None, f"行不存在：{run_id}"
        for key, value in fields.items():
            setattr(run, key, value)
        await session.commit()


async def add_review(run_id: str, *, status: str = "accepted", version: int = 1) -> None:
    async with get_session_factory()() as session:
        session.add(
            ReplacementReview(
                run_id=run_id,
                status=status,
                version=version,
                reason_codes=[],
                comment="",
                reviewed_by=ADMIN,
                reviewed_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def add_input(
    run_id: str,
    asset_id: str,
    *,
    role: str = "reference",
    position: int = 0,
):
    values: dict[str, Any] = {
        "run_id": run_id,
        "asset_id": asset_id,
        "role": role,
        "position": position,
        "usage": "",
        "storage_key": "",
    }
    async with get_session_factory()() as session:
        session.add(ReplacementRunInput(**values))
        await session.commit()


async def purge_runs() -> None:
    """清空全部替换任务相关行。

    列表类用例（任务中心分页、回收站计数）断言的是「总数」，而这个库是 session
    级共享的：上一条用例留下的行会让下一条的数字莫名多 1。清表比给每条用例
    写一遍 owner 隔离更诚实——它们本来就在验「所有人的记录怎么排」。
    """
    async with get_session_factory()() as session:
        for model in (ReplacementReview, ReplacementRunInput, ReplacementRun):
            await session.execute(delete(model))
        await session.commit()


async def drop_run(run_id: str) -> None:
    """删掉一条任务及其输入与评价：用例自己造的现场自己收。"""
    async with get_session_factory()() as session:
        for model in (ReplacementReview, ReplacementRunInput):
            await session.execute(delete(model).where(model.run_id == run_id))
        await session.execute(delete(ReplacementRun).where(ReplacementRun.id == run_id))
        await session.commit()


async def drop_pack(pack_id: str) -> None:
    async with get_session_factory()() as session:
        for model in (TemplatePackVersion, TemplatePackReference):
            await session.execute(delete(model).where(model.pack_id == pack_id))
        await session.execute(delete(TemplatePack).where(TemplatePack.id == pack_id))
        await session.commit()


async def add_pack(
    owner: str = ADMIN,
    *,
    pack_id: str | None = None,
    kind: str = "character",
    name: str = "测试封装",
    reference_ids: list[str] | None = None,
    roles: list[str] | None = None,
    required_roles: list[str] | None = None,
    scopes: list[str] | None = None,
    status: str = "published",
    version: int = 1,
    features: str = "",
    instructions: str = "",
    wardrobe_mode: str = "preserve",
) -> TemplatePack:
    """造一个封装行（可选带一份已发布版本）。

    `reference_ids` 原样写进引用表，**不校验素材是否存在**：一半的用例要验的正是
    「素材被删之后发布/准备该怎样」，那是业务层的判断，不该被夹具提前挡掉。
    """
    refs = list(reference_ids or [])
    bindings = list(roles or ["front"] * len(refs))
    pack = TemplatePack(
        id=pack_id or uuid.uuid4().hex,
        kind=kind,
        name=name,
        owner_id=owner,
        status=status,
        version=version if status == "published" else 0,
        revision=1,
        cover_asset_id=refs[0] if refs else None,
        features=features,
        instructions=instructions,
        wardrobe_mode=wardrobe_mode,
        required_roles=list(required_roles or ["front"]),
        supported_scopes=list(scopes or []),
        published_at=datetime.now(UTC) if status == "published" else None,
    )
    async with get_session_factory()() as session:
        session.add(pack)
        for position, asset_id in enumerate(refs):
            session.add(
                TemplatePackReference(
                    pack_id=pack.id,
                    asset_id=asset_id,
                    position=position,
                    role=bindings[position] if position < len(bindings) else "front",
                    description="",
                )
            )
        if pack.status == "published":
            session.add(
                TemplatePackVersion(
                    pack_id=pack.id,
                    version=pack.version,
                    reference_ids=refs,
                    bindings=[
                        {"role": bindings[index] if index < len(bindings) else "front",
                         "description": ""}
                        for index in range(len(refs))
                    ],
                    required_roles=list(pack.required_roles),
                    supported_scopes=list(pack.supported_scopes),
                    features=features,
                    instructions=instructions,
                    wardrobe_mode=wardrobe_mode,
                    published_at=pack.published_at,
                )
            )
        await session.commit()
        await session.refresh(pack)
    return pack


# --------------------------------------------------------------------------- #
# 工作区
# --------------------------------------------------------------------------- #
def workspace(run_id: str, state: dict[str, Any] | None = None) -> Path:
    """造 `run.json` + 目录 + 指针：三件缺一，执行器或详情页就找不到这条档案。

    必须在用例里 `monkeypatch` 过归档根之后调用——`runs_root()` 每次读配置。
    """
    path = runs_root() / f"{run_id}"
    ensure_layout(path)
    write_state(
        path,
        state if state is not None else {"id": run_id, "status": "prepared"},
    )
    save_pointer(get_archive_storage(), run_id, relative_key(path))
    return path


# --------------------------------------------------------------------------- #
# 素材字节
# --------------------------------------------------------------------------- #
def png_bytes(size: tuple[int, int] = (32, 48), color: str | None = None) -> bytes:
    """PNG 字节。颜色默认随机：`assets` 表跨用例共享，同字节会被摘要去重命中
    上一条用例的记录，而那条记录的字节在上一轮的临时目录里（已被清掉）。"""
    buffer = __import__("io").BytesIO()
    palette = color or "#" + os.urandom(3).hex()
    Image.new("RGB", size, palette).save(buffer, format="PNG")
    return buffer.getvalue()


def video_bytes(duration: float = 2, *, audio: bool = True) -> bytes:
    """H.264 短视频（默认带 AAC 音轨）。

    要真字节而不是占位：`prepare` 会读时长与音轨决定「能不能用」，拿假 MP4 会
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
    （图片 / 音频走 `/library/assets/upload`，视频原片走模板库专用的
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
