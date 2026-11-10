"""视频模板库 HTTP 契约。

路径挂在 `/templates` 下，与替换素材包、替换运行共用同一个模块与权限点：
读用 `template:read`，封装用 `template:pack:manage`（不新增权限码，
已有角色无需重新授权）。
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import SessionDep, require
from core import audit
from core.context import CurrentUser
from core.response import ApiResponse
from modules.template import video_template_store as store
from modules.template.video_template_schema import (
    VideoTemplateAttachSourceIn,
    VideoTemplateDraftIn,
    VideoTemplateListOut,
    VideoTemplateOut,
)

# 由 router.py 统一加 `/templates` 前缀，这里只写相对段（与 pack_router 一致）。
router = APIRouter(prefix="/video-templates", tags=["视频模板"])

ReadDep = Annotated[CurrentUser, Depends(require("template:read"))]
ManageDep = Annotated[
    CurrentUser, Depends(require("template:read", "template:pack:manage"))
]


@router.get("", summary="视频模板列表（固定蓝本优先）")
async def list_video_templates(
    actor: ReadDep,
) -> ApiResponse[VideoTemplateListOut]:
    # 读目录是同步文件 IO，进线程池；对应 `CODE_STYLE.md` §2.3。
    return ApiResponse.ok(await asyncio.to_thread(store.list_out))


@router.get("/{template_id}", summary="视频模板详情（含提示词与媒体顺序）")
async def get_video_template(
    template_id: str, actor: ReadDep
) -> ApiResponse[VideoTemplateOut]:
    return ApiResponse.ok(await asyncio.to_thread(store.get_out, template_id))


@router.post("", summary="封装一条视频模板", openapi_extra={"x-template-key-idempotency": 1})
async def create_video_template(
    payload: VideoTemplateDraftIn, actor: ManageDep, session: SessionDep
) -> ApiResponse[VideoTemplateOut]:
    return ApiResponse.ok(await store.create(payload, session))


@router.post("/{template_id}/pin", summary="设为固定蓝本（全库仅一个）")
async def pin_video_template(
    template_id: str, actor: ManageDep
) -> ApiResponse[VideoTemplateOut]:
    # 切的是**全库**蓝本：所有封装者下次都以它复制，属「发布级」管理动作，
    # 与 pack publish/disable 同一口径留审计；create/attach 只算保存，不记。
    result = await asyncio.to_thread(store.pin, template_id)
    await audit.record(
        "template.video_template.pin",
        actor_id=actor.id,
        target_type="video_template",
        target_id=template_id,
        detail={"name": result.name, "packId": result.pack_id},
    )
    return ApiResponse.ok(result)


@router.post("/{template_id}/attach-source", summary="绑定样片原片资产")
async def attach_video_template_source(
    template_id: str,
    payload: VideoTemplateAttachSourceIn,
    actor: ManageDep,
    session: SessionDep,
) -> ApiResponse[VideoTemplateOut]:
    """绑定后，这条模板对应的原片才会在「全部模板」里被当作可选卡片。"""
    return ApiResponse.ok(
        await store.attach_source(template_id, payload.source_video_asset_id, session)
    )
