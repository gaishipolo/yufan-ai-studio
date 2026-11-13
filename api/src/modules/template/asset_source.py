"""按 id 空间**显式分流**地取素材展示名（dual-read 兼容期唯一的分流点）。

## 为什么需要这一层

v1 `assets.id` 与 v2 `library_assets.id` 都是 32 位 uuid hex，形状不可区分。
`core/ports/library_lookup.py:9-11` 明令禁止「先查 v2、查不到回退 v1」的隐式兜底——
那会让某一侧悄悄读到另一半数据，而且只在删除之后才暴露成裂图。所以调用方必须
把**空间**一起交进来，由这里按空间各问各的端口。

## 为什么不返回元数据快照

现在唯一的消费方是「给媒体清单填展示名」（`video_template_service`），它只需要
`name`。把 `StoredMedia` 与 `LibraryAssetRef` 揉成一个统一快照，等于在这一层
重新定义「什么算已删除 / 谁有权限」——那正是 `readable_ids()` 的职责，两处判据
必然漂移，且漂移方向总是「多给权限」。等真正需要统一快照时（授权闸切换），
应当连同 `readable_ids()` 一起设计，而不是在这里顺手长出第二份规则。

## 名字不是授权

`get_many()` 不判可读性：拿到别人的 id 就能读到名字。这可以接受，因为这里的
名字只用于**给调用方已经持有的 id 填展示名**（模板/封装自己的参考图），而
「这张图能不能用」由授权闸与 `readable_ids()` 决定。反过来，若在这里顺手
加一层过滤，就会造出「名字有、图片没有」的第二种不一致，比泄漏一个文件名更难查。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.library_lookup import get_library_lookup
from core.ports.media import get_asset_lookup
from modules.template.id_space import ASSET_ID_SPACE_V1, ASSET_ID_SPACE_V2

__all__ = ["resolve_names"]


async def resolve_names(
    session: AsyncSession, refs: Iterable[tuple[str | None, str]]
) -> dict[str, str]:
    """`[(id, 空间)]` → `{id: 展示名}`，两套 id 混在一批里也照办。

    - 不存在的 id 不出现在结果里（调用方自己决定回落到什么，通常是回落成 id）；
    - 未知空间名忽略：这一列只可能是 `v1` / `v2`（列默认值 + 迁移回填），
      真出现第三种说明数据被改坏了。这里不抛，因为「取不到名字」在界面上是
      可见的（显示成裸 id），而为一个坏标记挡住整次保存没有收益。

    结果按 id 扁平化，前提是在这个系统里同一个 id 不会同时属于两套空间
    （两套表各自发号，都是 32 位随机 hex）。
    """
    wanted: dict[str, set[str]] = {ASSET_ID_SPACE_V1: set(), ASSET_ID_SPACE_V2: set()}
    for asset_id, space in refs:
        if asset_id and space in wanted:
            wanted[space].add(asset_id)

    names: dict[str, str] = {}
    if wanted[ASSET_ID_SPACE_V1]:
        stored = await get_asset_lookup().get_many(
            session, sorted(wanted[ASSET_ID_SPACE_V1])
        )
        names.update({asset_id: row.name for asset_id, row in stored.items()})
    if wanted[ASSET_ID_SPACE_V2]:
        library = await get_library_lookup().get_many(
            session, sorted(wanted[ASSET_ID_SPACE_V2])
        )
        names.update({asset_id: row.name for asset_id, row in library.items()})
    return names
