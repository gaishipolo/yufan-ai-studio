"""模板库的演示数据：一个已发布的「人物替换」封装 + 一个未完成的「产品替换」草稿。

**为什么要有两个**：只给一个已发布的包，界面上看不到「还差什么」这条最重要的
提示；只给草稿，模板库里没有可用对象，替换流程走不动。两个一起给，第一屏就能
同时看到「可用的」和「要补的」。

**素材用 SQL 直查 `assets` 表**，不 import 素材模块的模型：模块之间不许互相
import（`EXTENDING.md` §5.1）。seed 是运维工具，读一条 `SELECT id` 不构成依赖。
查不到素材时只种草稿——`app seed` 在只跑本模块的环境里也该成功。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from modules.template.models import TemplatePack, TemplatePackReference, TemplatePackVersion

# (角色用途, 说明)。顺序就是提示词里的编号顺序，别随手调。
_CHARACTER_BINDINGS = (
    ("front", "同一位真人，正面清晰"),
    ("side", "同一人侧脸，光线一致"),
    ("fullbody", "同一人全身，用于确认身高比例"),
)

_PRODUCT_BINDINGS = (
    ("front", "产品正面，白底"),
    ("detail", "只补扣件与缝线"),
)


async def seed(session: AsyncSession, context: SeedContext) -> int:
    character_ids = await _asset_ids(session, context.demo_user_id, "character", 3)
    product_ids = await _asset_ids(session, context.demo_user_id, "product", 2)
    created = 0

    # ---------- 已发布：人物替换 ----------
    if len(character_ids) >= 1:
        pack = TemplatePack(
            kind="character",
            name="示例人物 · 通勤白衬衫",
            owner_id=context.demo_user_id,
            status="published",
            version=1,
            revision=1,
            cover_asset_id=character_ids[0],
            features="同一人物在不同镜头里保持发型、肤色与面部特征一致。",
            instructions="不要改变原片人物的动作与背景。",
            required_roles=["front"],
            published_at=datetime.now(UTC),
        )
        session.add(pack)
        await session.flush()
        _bind(session, pack.id, character_ids, _CHARACTER_BINDINGS)
        session.add(
            TemplatePackVersion(
                pack_id=pack.id,
                version=1,
                reference_ids=character_ids,
                bindings=[
                    {"role": role, "description": desc}
                    for role, desc in _CHARACTER_BINDINGS[: len(character_ids)]
                ],
                required_roles=["front"],
                supported_scopes=[],
                features=pack.features,
                instructions=pack.instructions,
                wardrobe_mode="preserve",
                published_at=pack.published_at,
            )
        )
        created += 1

    # ---------- 草稿：产品替换（刻意缺「替换范围」，让 gaps 有东西可说） ----------
    if product_ids:
        draft = TemplatePack(
            kind="product",
            name="示例产品 · 待补替换范围",
            owner_id=context.demo_user_id,
            status="draft",
            version=0,
            revision=1,
            cover_asset_id=product_ids[0],
            features="",
            instructions="",
            required_roles=["front"],
            supported_scopes=[],
        )
        session.add(draft)
        await session.flush()
        _bind(session, draft.id, product_ids, _PRODUCT_BINDINGS)
        created += 1

    await session.commit()
    return created


def _bind(session: AsyncSession, pack_id: str, asset_ids: list[str], bindings) -> None:
    for position, (asset_id, (role, description)) in enumerate(
        zip(asset_ids, bindings, strict=False)
    ):
        session.add(
            TemplatePackReference(
                pack_id=pack_id,
                asset_id=asset_id,
                position=position,
                role=role,
                description=description,
            )
        )


async def _asset_ids(
    session: AsyncSession, owner_id: str, category: str, limit: int
) -> list[str]:
    """取本人某类素材的 id。没导入素材模块时这张表可能为空，返回空列表。"""
    rows = await session.execute(
        text(
            "SELECT id FROM assets "
            "WHERE owner_id = :owner AND category = :category "
            "AND type = 'image' AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT :limit"
        ),
        {"owner": owner_id, "category": category, "limit": limit},
    )
    return [str(row[0]) for row in rows]
