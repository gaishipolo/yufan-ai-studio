"""视频模板落表后，测试里造数据的唯一入口。

`video_template_store.py` 时期，测试靠「把 `CATALOG_PATH` 指向 tmp_path 再写一份
JSON」隔离数据。落表之后隔离改由数据库负责，但测试库是**整场共用的一个 sqlite
文件**，所以每个用例仍然要自己把用到的行写进去、并且只断言自己那几行。

三个约定：

- `record()` 是模板的「标准样本」，字段与 `VideoTemplateOut` 对齐（camelCase）；
- `put()` 幂等写入（同 id 覆盖），用例开头写、断言时按 id 取，互不干扰；
- `run()` 把 coroutine 跑在独立的事件循环里，给「不走 TestClient」的端口级用例用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import update

from db.session import get_session_factory
from modules.template_pack.models import VideoTemplate
from modules.template_pack.video_template_schema import VideoTemplateOut
from modules.template_pack.video_template_service import apply_out, to_out

T = TypeVar("T")

NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")
SOURCE_ID = "ac1d588d36294fa49c809d46bb1dbb6d"
PACK_ID = "1cbf52ffeeb044f1ad2cc84aa484b941"
IMAGES = ["front-asset", "multiview-asset", "detail-asset"]


def run(coro: Coroutine[Any, Any, T]) -> T:
    """跑一个 coroutine。测试之间没有共享 loop，每个用例自建自关。"""
    return asyncio.run(coro)


def record(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "template-1",
        "templateKey": "milan_night_3030_pink",
        "name": "米兰之夜 · 3030 粉色",
        "description": "",
        "prompt": "对 {{Mixed 1}} 做替换，以 {{Mixed 3}} 三视图为准。",
        "promptSha256": "0" * 64,
        "sourceVideoName": "我穿的是米兰之夜模板测试.mp4",
        "sourceVideoAssetId": SOURCE_ID,
        "sourceVideoPath": "",
        "sourceDurationSec": 5.312,
        "aspectRatio": "9:16",
        "packId": PACK_ID,
        "packName": "3030 粉色",
        "packVersion": 2,
        "defaultKind": "product",
        "mediaOrder": [
            {"token": "{{Mixed 1}}", "role": "source_video", "type": "video", "name": "样片"},
            *[
                {
                    "token": f"{{{{Mixed {index}}}}}",
                    "role": "multiview",
                    "type": "image",
                    "assetId": asset_id,
                    "name": asset_id,
                }
                for index, asset_id in enumerate(IMAGES, 2)
            ],
        ],
        "generation": {"duration": 5, "ratio": "9:16"},
        "rules": {"promptMode": "fixed_no_compilation"},
        "coverUrl": "",
        "pinned": True,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def row_from(data: dict[str, Any]) -> VideoTemplate:
    """camelCase 字典 → 待插入的 ORM 行。

    复用 `apply_out()` 而不是再写一份字段映射：两处映射迟早漂移，而漂移的表现是
    「测试通过、线上少一列」。
    """
    out = VideoTemplateOut.model_validate(data)
    row = VideoTemplate(
        id=out.id,
        template_key=out.template_key,
        created_at=out.created_at,
        updated_at=out.updated_at,
    )
    apply_out(row, out)
    return row


async def _put(data: dict[str, Any]) -> VideoTemplateOut:
    wanted = VideoTemplateOut.model_validate(data)
    async with get_session_factory()() as session:
        if wanted.pinned:
            # 测试库整场共用：别的用例留下的蓝本会让这一行撞
            # `uq_video_templates_pinned`。写入前先让位，与生产 `create` 同口径。
            await session.execute(
                update(VideoTemplate)
                .where(VideoTemplate.pinned.is_(True), VideoTemplate.id != wanted.id)
                .values(pinned=False)
            )
        row = await session.get(VideoTemplate, wanted.id)
        if row is None:
            row = row_from(data)
            session.add(row)
        else:
            apply_out(row, wanted)
        await session.commit()
        return to_out(row)


async def aput(**overrides: object) -> VideoTemplateOut:
    """`put()` 的 async 版。

    `async def` 的用例里**不能**用同步版：它内部 `asyncio.run()`，在已运行的事件
    循环里会直接抛 `RuntimeError: asyncio.run() cannot be called from a running
    event loop`；而 pytest 把这种抛错报在「用例末尾回收协程」的位置，读起来像
    无关的 flaky。
    """
    return await _put(record(**overrides))


def put(**overrides: object) -> VideoTemplateOut:
    """写入一条模板（同 id 覆盖）。同步用例开头造数据用。"""
    return run(aput(**overrides))


async def _delete(template_id: str) -> None:
    async with get_session_factory()() as session:
        row = await session.get(VideoTemplate, template_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def aremove(template_id: str) -> None:
    """`remove()` 的 async 版。"""
    await _delete(template_id)


def remove(template_id: str) -> None:
    run(aremove(template_id))


async def _unpin_all() -> None:
    async with get_session_factory()() as session:
        await session.execute(update(VideoTemplate).values(pinned=False))
        await session.commit()


def unpin_all() -> None:
    """把全库的蓝本标记清空。

    给「不由 `put()` 写入」的用例用：导入、迁移这类直接往表里塞行的路径
    不会先让位，撞上别人留下的蓝本会以 `IntegrityError` 形式报错 —— 那读起来
    像「导入坏了」，其实是测试库共用的副作用。
    """
    run(_unpin_all())


async def _count() -> int:
    from sqlalchemy import func, select

    async with get_session_factory()() as session:
        return int(await session.scalar(select(func.count()).select_from(VideoTemplate)) or 0)


def count() -> int:
    return run(_count())


# --------------------------------------------------------------------------- #
# 服务层快捷方式：让用例只关心断言，不用每次拼 session
# --------------------------------------------------------------------------- #
def put_template(template_id: str, **overrides: object):
    """按 `record()` 的样板写一条模板（同 id 覆盖），返回出参。"""
    data = record(id=template_id, templateKey=template_id, pinned=False)
    data.update(overrides)
    return put(**data)


def get_template(template_id: str):
    from modules.template_pack import video_template_service as service

    async def go():
        async with get_session_factory()() as session:
            return await service.get_out(session, template_id)

    return run(go())


def update_prompt(template_id: str, prompt: str, fixed: list[dict] | None = None):
    from modules.template_pack import video_template_service as service

    async def go():
        async with get_session_factory()() as session:
            return await service.update_prompt(template_id, prompt, fixed, session=session)

    return run(go())
