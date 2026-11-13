"""参考图（v2 资产）的受理期校验：一条判据，两条生成链路共用。

视频任务把「用了哪几张图」记进自己的行，生图作业把同一批 id 解析成快照再交给
供应商。两边的第一道闸门是同一个问题：**这批 id 现在能不能当参考图**。

以前只有生图有这道检查（`image/service.py::_resolve_references`），视频任务
直接把前端给的 id 落库。而 id 不是所有权凭证：能拿别人的私有素材当参考图，
等于让供应商替我们复制一份再取回来，共享审批被整条绕过。

## 四道判据，缺一不可

1. **存在**：`get_many` 里没有 = 已删除（或从来没存在过）；
2. **可读**：`readable_ids` 是唯一判据（`modules/library/access.py` 一份），
   在这里重写一遍必然漂移，漂移的方向总是「多给权限」；
3. **在库**：`state is ACTIVE`。「读得到」不等于「能用来生成」——`can_read`
   对回收站放行 owner、对草稿放行 owner（还放行代审权），照抄就会让
   「删除」在生成链路上不生效；
4. **类型受支持**：默认只允许 `IMAGE`（生图、历史视频任务都是这条口径）。
   视频替换模型要收「原片视频 + 参考图 + 音频」，由 `modules/task` 显式传
   `kinds` 放宽——默认值收窄是故意的，免得别的调用方顺手把视频交给纯生图接口。

`text_library_ids` 不在这里：文本是**正文内联**，走 `core.library_text`。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Forbidden
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetRef,
    LibraryAssetState,
    get_library_lookup,
)

__all__ = ["normalize_reference_ids", "resolve_reference_images"]


def normalize_reference_ids(asset_ids: Sequence[str]) -> list[str]:
    """去空白、去重保序。顺序有意义：多图合成里的「第一张」指的就是它。"""
    return [item for item in dict.fromkeys(raw.strip() for raw in asset_ids) if item]


async def resolve_reference_images(
    session: AsyncSession,
    actor: CurrentUser,
    asset_ids: Sequence[str],
    *,
    limit: int | None = None,
    kinds: Sequence[LibraryAssetKind] = (LibraryAssetKind.IMAGE,),
) -> list[LibraryAssetRef]:
    """按入参顺序返回可用的参考图快照；任何一条不合格就整批拒绝。

    不做「跳过不合格的那几张」：提示词里的「第一张 / 第二张」与提交顺序绑定，
    悄悄少一张会让整段描述错位，而且是查不出来的错位。

    `limit` 由调用方给（生图看模型的 `maxReferences`，视频看接口契约），
    留空表示不在这里卡张数。`kinds` 默认只允许图片；视频替换模型还要收
    原片视频与音频，由 `modules/task` 显式放宽——默认值收窄是故意的，
    免得别的调用方因为一次「顺手放宽」把视频当参考图交给纯生图接口。
    """
    wanted = normalize_reference_ids(asset_ids)
    if not wanted:
        return []
    if limit is not None and len(wanted) > limit:
        raise BadRequest(f"最多接受 {limit} 张参考图，本次传了 {len(wanted)} 张")

    lookup = get_library_lookup()
    found = await lookup.get_many(session, wanted)
    missing = [asset_id for asset_id in wanted if asset_id not in found]
    if missing:
        raise BadRequest(f"参考图不存在或已被删除：{missing}")

    readable = await lookup.readable_ids(session, actor, wanted)
    denied = [asset_id for asset_id in wanted if asset_id not in readable]
    if denied:
        raise Forbidden(f"无权限使用这些参考图（可能是他人的私有素材）：{denied}")

    unavailable = [
        asset_id for asset_id in wanted if found[asset_id].state is not LibraryAssetState.ACTIVE
    ]
    if unavailable:
        raise BadRequest(f"参考图不可用（在回收站或尚未采纳的草稿里）：{unavailable}")

    for asset_id in wanted:
        ref = found[asset_id]
        if ref.kind not in kinds:
            raise BadRequest(
                f"参考素材 {ref.name or asset_id} 的类型不受支持（当前为 {ref.kind.value}）"
            )

    return [found[asset_id] for asset_id in wanted]
