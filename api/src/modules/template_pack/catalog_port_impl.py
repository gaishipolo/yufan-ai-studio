"""`core.ports.template_catalog` 的实现：把已发布配置组装成 core 层快照。

只做「读 + 投影」，不写库、不判权限、不触发生成。端口的契约是
「已发布数据长什么样」，可见性判据不在这里重写一份。

## C1 边界

`get_pack()` 的 `version` 目前只认当前发布版本：历史版本快照表
（`template_pack_versions`）还属于 `modules/template`，阶段 C2 迁进来之后
才补齐「按 version 取不可变快照」。传入不匹配的 version 返回 `None`
而不是悄悄给最新一版 —— 「要 v2 却拿到 v4」比「没有」危险得多。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.template_catalog import (
    CatalogPack,
    CatalogReference,
    CatalogTemplate,
    register_template_catalog,
)
from modules.template_pack.models import (
    TemplateManifest,
    TemplateManifestFunction,
    TemplateManifestPack,
    TemplateManifestReference,
)

__all__ = ["TemplateCatalogImpl", "install"]


def _rules(row: TemplateManifestFunction) -> dict[str, Any]:
    rules: dict[str, Any] = {
        "workbenchLabel": row.name,
        "scopeOptions": list(row.scope_options or []),
    }
    if row.input_mode == "video_scene_text":
        rules.update(
            inputMode="video_scene_text",
            scenePromptDefault=row.scene_prompt_default or "",
        )
    if row.bindings:
        rules["referenceBindings"] = dict(row.bindings)
    return rules


def _template(
    row: TemplateManifestFunction, head: TemplateManifest
) -> CatalogTemplate:
    return CatalogTemplate(
        id=row.id,
        template_key=f"{head.template_key}:{row.function_key}",
        name=f"{head.name} · {row.name}",
        kind=row.kind,
        prompt=row.prompt,
        rules=_rules(row),
        media_order=tuple(row.media_order or []),
        bindings=dict(row.bindings or {}),
        scope_options=tuple(row.scope_options or []),
        aspect_ratio=row.aspect_ratio,
        duration=row.duration,
        source_video_asset_id=head.video_asset_id,
        pack_id="",
        pack_version=0,
        published_at=head.published_at,
    )


class TemplateCatalogImpl:
    """只读实现。无状态，进程单例即可。"""

    async def list_templates(
        self, session: AsyncSession, *, kind: str = ""
    ) -> list[CatalogTemplate]:
        stmt = (
            select(TemplateManifestFunction, TemplateManifest)
            .join(
                TemplateManifest,
                TemplateManifestFunction.manifest_id == TemplateManifest.id,
            )
            .where(
                TemplateManifest.status == "published",
                TemplateManifest.deleted_at.is_(None),
            )
        )
        if kind:
            stmt = stmt.where(TemplateManifestFunction.kind == kind)
        rows = (await session.execute(stmt)).all()
        return [_template(fn, head) for fn, head in rows]

    async def get_template(
        self, session: AsyncSession, template_id: str
    ) -> CatalogTemplate | None:
        row = (
            await session.execute(
                select(TemplateManifestFunction, TemplateManifest)
                .join(
                    TemplateManifest,
                    TemplateManifestFunction.manifest_id == TemplateManifest.id,
                )
                .where(
                    TemplateManifestFunction.id == template_id,
                    TemplateManifest.status == "published",
                    TemplateManifest.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        fn, head = row
        return _template(fn, head)

    async def get_pack(
        self, session: AsyncSession, pack_id: str, version: int | None = None
    ) -> CatalogPack | None:
        row = (
            await session.execute(
                select(TemplateManifestPack, TemplateManifest)
                .join(
                    TemplateManifest,
                    TemplateManifestPack.manifest_id == TemplateManifest.id,
                )
                .where(
                    TemplateManifestPack.id == pack_id,
                    TemplateManifest.status == "published",
                    TemplateManifest.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        pack, head = row
        if version is not None and version != head.version:
            return None
        refs = (
            await session.scalars(
                select(TemplateManifestReference)
                .where(TemplateManifestReference.pack_id == pack.id)
                .order_by(TemplateManifestReference.position)
            )
        ).all()
        return CatalogPack(
            id=pack.id,
            name=pack.name,
            kind=pack.kind,
            version=head.version,
            required_roles=tuple(pack.required_roles or []),
            supported_scopes=tuple(pack.supported_scopes or []),
            wardrobe_mode=pack.wardrobe_mode,
            references=tuple(
                CatalogReference(
                    asset_id=ref.asset_id,
                    ref_key=ref.ref_key,
                    role=ref.role,
                    description=ref.description,
                    position=ref.position,
                )
                for ref in refs
            ),
        )


def install() -> None:
    """在模块 import 期调用：注册是副作用，不调用端口就永远没实现。"""
    register_template_catalog(TemplateCatalogImpl())
