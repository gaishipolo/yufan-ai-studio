"""模板库的演示数据：一个已发布的「人物替换」封装 + 一个未完成的「产品替换」草稿。

**为什么要有两个**：只给一个已发布的包，界面上看不到「还差什么」这条最重要的
提示；只给草稿，模板库里没有可用对象，替换流程走不动。两个一起给，第一屏就能
同时看到「可用的」和「要补的」。

**素材走 `LibraryLookup`（v2 注入口），不 import 素材模块的模型，也不写裸 SQL**：
模块之间不许互相 import（`EXTENDING.md` §5.1）。以前这里是一条
`SELECT id FROM assets WHERE category = ...`——那既绕过了端口，又把 v1 的 `category`
概念带进了模板模块；v2 没有分类列（分类是封装时的槽位绑定，见计划 §22），
所以改成「按 `kind` 取一批图再切片」。
查不到素材时只种草稿——`app seed` 在只跑本模块的环境里也该成功。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.module import SeedContext
from core.context import CurrentUser
from core.logging import get_logger
from core.ports.library_lookup import LibraryAssetKind, get_library_lookup
from modules.template.id_space import ASSET_ID_SPACE_V2
from modules.template.models import TemplatePack, TemplatePackReference, TemplatePackVersion

logger = get_logger(__name__)

#: 一次取够两个封装要用的图。v2 没有分类，两条演示封装拿同一批图的不同切片：
#: 演示数据要证明的是「有两种封装形态」，不是「这些图属于某个产品」。
_DEMO_IMAGE_LIMIT = 5
_CHARACTER_SLICE = 3

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
    image_ids = await _asset_ids(session, context)
    character_ids = image_ids[:_CHARACTER_SLICE]
    product_ids = image_ids[_CHARACTER_SLICE:]
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
            cover_asset_id_space=ASSET_ID_SPACE_V2,
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
                reference_ids_space=ASSET_ID_SPACE_V2,
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
            cover_asset_id_space=ASSET_ID_SPACE_V2,
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
                asset_id_space=ASSET_ID_SPACE_V2,
                description=description,
            )
        )


async def _asset_ids(session: AsyncSession, context: SeedContext) -> list[str]:
    """取演示账号能读到的一批图片 id（v2）。

    `find_ids` 已经做了三件事：`state=active` 硬过滤、按 `kind` 筛、逐条过
    `readable_ids()`。所以这里既不会挑到草稿 / 回收站，也不会把别人的私有图
    塞进演示封装。

    **空列表时打一条 WARNING**：v2 资产库没有演示素材时，`app seed` 只会种出
    一个草稿封装，第一屏看不到「可用的包」。这不是致命错误（seed 该成功），
    但必须看得见——否则下一次有人问「为什么演示数据少了一半」时会从模板模块查起。
    """
    actor = CurrentUser(
        id=context.demo_user_id, name=context.demo_user_name, permissions=frozenset()
    )
    asset_ids = await get_library_lookup().find_ids(
        session, actor, kind=LibraryAssetKind.IMAGE, limit=_DEMO_IMAGE_LIMIT
    )
    if not asset_ids:
        logger.warning(
            "资产库 v2 没有可用的演示图片，模板演示数据只种草稿封装"
            "（跑 `app library import-archive` 或先上传素材再 `app seed`）"
        )
    return list(asset_ids)
