"""`template-runtime` 端口的临时实现：直接读本模块（`modules/template`）的表。

**这是搬迁过程中的过渡态**：素材包 / 视频模板的表现在还在本模块，所以由本模块
注册实现。等 C2 把这些表整体搬进 `modules/template_pack`，这个文件随表一起搬，
`register_template_runtime()` 的调用点从 `modules/template/__init__.py` 换成
`modules/template_pack/__init__.py` —— 消费方（`replacement_service` /
`authorization` / `template_references`）一行都不用改。

为什么现在就落这一层、而不是搬完再抽：先让执行链**不能**再直接摸封装的仓储与
ORM 行，搬迁时剩下的就只是「文件挪位置 + 换注册点」，不会一边挪一边改语义。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.library_lookup import LibraryAssetRef
from core.ports.template_runtime import (
    RuntimePack,
    RuntimePackVersion,
    RuntimeTemplate,
    RuntimeTemplateMedia,
)
from modules.template_pack import video_template_service
from modules.template_pack.models import (
    TemplatePack,
    TemplatePackVersion,
)
from modules.template_pack.repository import PackRepository
from modules.template_pack.video_template_repository import VideoTemplateRepository
from modules.template_pack.video_template_schema import VideoTemplateOut

__all__ = ["TemplateRuntime", "to_runtime"]


def _pack(row: TemplatePack) -> RuntimePack:
    return RuntimePack(
        id=row.id,
        name=row.name or "",
        kind=row.kind or "",
        owner_id=row.owner_id or "",
        status=row.status or "",
        version=int(row.version or 0),
        deleted=row.deleted_at is not None,
    )


def _version(row: TemplatePackVersion) -> RuntimePackVersion:
    return RuntimePackVersion(
        pack_id=row.pack_id,
        version=int(row.version or 0),
        reference_ids=tuple(row.reference_ids or ()),
        # 逐图用途原样带出（`{role, description}`），消费方按位置对齐读取；
        # 这里不校验长度，坏数据在 prepare 的 `zip(strict=True)` 处会当场暴露。
        bindings=tuple(dict(item) for item in (row.bindings or ())),
        supported_scopes=tuple(row.supported_scopes or ()),
        features=row.features or "",
        instructions=row.instructions or "",
        wardrobe_mode=row.wardrobe_mode or "preserve",
    )


def to_runtime(out: VideoTemplateOut) -> RuntimeTemplate:
    """出参 → 执行链快照。

    **生产与测试共用这一处映射**：测试里造完模板也要交给执行链的纯函数，
    各写一份就会「测试通过、线上少一列」。
    """
    return RuntimeTemplate(
        id=out.id,
        name=out.name,
        prompt=out.prompt,
        prompt_sha256=out.prompt_sha256,
        rules=dict(out.rules or {}),
        media_order=tuple(
            RuntimeTemplateMedia(
                type=str(item.type or "image"),
                role=str(item.role or ""),
                asset_id=str(item.asset_id or ""),
                in_prompt=bool(item.in_prompt),
            )
            for item in out.media_order
        ),
        generation=dict(out.generation or {}),
        aspect_ratio=out.aspect_ratio,
        default_kind=str(out.default_kind or "product"),
        pack_id=str(out.pack_id or ""),
        pack_version=int(out.pack_version or 0),
        source_video_asset_id=str(out.source_video_asset_id or ""),
    )


class TemplateRuntime:
    """无状态实现：每次调用自带 session，实例本身可以全局共享。"""

    async def get_pack(self, session: AsyncSession, pack_id: str) -> RuntimePack | None:
        row = await PackRepository(session).get(pack_id)
        return None if row is None else _pack(row)

    async def get_pack_version(
        self, session: AsyncSession, pack_id: str, version: int
    ) -> RuntimePackVersion | None:
        row = await PackRepository(session).get_version(pack_id, version)
        return None if row is None else _version(row)

    async def available_packs(self, session: AsyncSession) -> list[dict[str, Any]]:
        # 惰性 import：`pack_service` 自己依赖业务 schema，放在模块顶部会让
        # `modules.template` 的包初始化多绕一圈（`__init__` 已 import 了 router）。
        from modules.template_pack.pack_service import PackService

        packs = await PackService(session).available()
        return [pack.model_dump(by_alias=True) for pack in packs]

    async def find_template_for_run(
        self,
        session: AsyncSession,
        *,
        source_asset_id: str,
        pack_id: str | None = None,
        kind: str | None = None,
        template_id: str | None = None,
    ) -> RuntimeTemplate | None:
        out = await video_template_service.find_for_run(
            source_asset_id,
            pack_id,
            kind,
            template_id,
            session=session,
        )
        return None if out is None else to_runtime(out)

    async def template_names(
        self, session: AsyncSession, template_ids: Sequence[str]
    ) -> dict[str, str]:
        return await VideoTemplateRepository(session).names(template_ids)

    async def resolve_fixed_references(
        self,
        session: AsyncSession,
        references: Sequence[Mapping[str, Any]],
        *,
        actor: CurrentUser,
    ) -> dict[str, LibraryAssetRef]:
        from modules.template_pack.template_references import resolve_pack_references
        from modules.template_pack.video_template_schema import VideoTemplatePackReferenceIn

        parsed = [VideoTemplatePackReferenceIn.model_validate(item) for item in references]
        _, resolved = await resolve_pack_references(session, parsed, actor)
        return resolved
