"""已发布封装中的固定图片引用；元数据和权限只从服务端解析。

**与 `create` 的口径差异是有意为之**：`VideoTemplateStore.create` 要求
「封装处理类型必须与所选素材包类型一致」，而 `PATCH /video-templates/{id}/prompt`
允许把任意 kind 的已发布包图片固定到任意 default_kind 的模板上（回归用例
`test_product_template_uses_fixed_image_from_another_kind_of_pack` 锁定了这个行为）。
本模块只校验「图片分类 == 它所属包的分类」，**不校验包 kind 与模板 kind 的关系**。
若将来要收紧，必须同时修改 create 的约束、这条注释和上述用例，不能只改一处。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest
from core.media import StoredMedia
from core.ports.media import get_asset_lookup
from modules.template.repository import PackRepository
from modules.template.video_template_schema import VideoTemplatePackReferenceIn


async def resolve_pack_references(
    session: AsyncSession,
    references: list[VideoTemplatePackReferenceIn],
) -> tuple[list[dict[str, Any]], dict[str, StoredMedia]]:
    repo = PackRepository(session)
    media = await get_asset_lookup().get_many(session, [ref.asset_id for ref in references])
    result: list[dict[str, Any]] = []
    resolved: dict[str, StoredMedia] = {}
    seen: set[str] = set()
    for ref in references:
        pack = await repo.get(ref.pack_id)
        frozen = await repo.get_version(ref.pack_id, ref.pack_version)
        row = media.get(ref.asset_id)
        if (
            pack is None
            or pack.deleted_at
            or pack.status != "published"
            or frozen is None
            or ref.asset_id not in frozen.reference_ids
        ):
            raise BadRequest("引用的封装已停用、版本不可用或图片不在该发布版本中")
        if (
            row is None
            or row.deleted_at
            or row.type != "image"
            or row.owner_id != pack.owner_id
            or row.category != pack.kind
        ):
            raise BadRequest("封装参考图不可用，请重新选择")
        if ref.asset_id in seen or ref.token in resolved:
            raise BadRequest("固定参考图或引用编号不能重复")
        seen.add(ref.asset_id)
        url = (
            f"/api/v1/templates/replacement-packs/{pack.id}/preview/{row.id}"
            f"?version={ref.pack_version}"
        )
        result.append(
            {
                **ref.model_dump(by_alias=True),
                "name": row.name,
                "packName": pack.name,
                "type": "image",
                "url": url,
            }
        )
        resolved[ref.token] = row
    return result, resolved
