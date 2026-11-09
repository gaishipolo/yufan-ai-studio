"""可见性判定：读、共享、删除三条规则共用同一份定义。

这个文件存在的唯一理由是**同一条规则不能写第二遍**：

- 列表按可见集过滤（SQL 里）；
- 详情 / 取件按可见集判定（Python 里，一行数据）；
- 共享申请要判断「这个目标对这张资产成不成立」；
- 删除矩阵要知道「已共享到哪、我是不是组长」。

四处的判定如果各写各的，最先漂移的总是「小组可见」这一支：列表漏了
`state=active`，于是回收站里的共享残影出现在小组库；或者判定用了旧组成员，
被移出小组的人还能看到。所以 SQL 分支与 Python 分支都从这里的常量与函数取。

## 两条口径

1. **草稿与回收站永远 personal**（不变式 A）。所以它们的可见集不参与小组/团队
   判定，只按 `owner_id`（外加管理员兜底权限）收窄。
2. **团队 ⊇ 小组**：`visibility=team` 时 `library_asset_groups` 里不该有行。
   共享到团队会先清空小组行（见 `sharing.apply_share`），否则「撤销某个组」
   会在团队共享上留下说不清的状态。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotFound
from core.ports.org_directory import group_ids_of, group_names, user_names
from modules.library.models import LibraryAsset, LibraryAssetGroup, LibraryGroupLeader
from modules.library.schema import LibraryTab

__all__ = [
    "active_visible_clause",
    "all_groups",
    "asset_group_ids",
    "can_read",
    "group_names_for",
    "is_group_leader",
    "leader_group_ids",
    "load_asset",
    "load_visible",
    "owner_names",
    "tab_clause",
]

#: 草稿箱代审范围：持这个码的人能看到别人的草稿（否则只看自己的）。
PERM_DRAFT_REVIEW = "library:draft:review"
#: 回收站与团队库的兜底通道：删别人的、删团队库的、彻底删除。
PERM_DELETE_ANY = "library:delete:any"


async def all_groups(session: AsyncSession, actor: CurrentUser) -> list[str]:
    """我所属的小组。走组织目录注入口，未接上时为空（小组库为空，不多看见）。"""
    return await group_ids_of(session, actor.id)


def active_visible_clause(
    actor: CurrentUser, groups: Sequence[str]
) -> ColumnElement[bool]:
    """`state=active` 前提下「我能看到」的 SQL 条件。

    三支并集：我自己的（且未共享）、共享到我所在小组的、共享给团队的。
    「我自己的」刻意只算 `visibility=personal`：一条素材共享出去之后，它在
    小组库里，个人库里不再重复出现——否则同一张图会在两个入口各出现一次，
    用户会以为是两份。
    """
    group_branch = LibraryAsset.id.in_(
        select(LibraryAssetGroup.asset_id).where(
            LibraryAssetGroup.group_id.in_(list(groups))
        )
    )
    return or_(
        and_(
            LibraryAsset.owner_id == actor.id,
            LibraryAsset.visibility == "personal",
        ),
        and_(LibraryAsset.visibility == "group", group_branch),
        LibraryAsset.visibility == "team",
    )


def tab_clause(
    actor: CurrentUser, groups: Sequence[str], tab: LibraryTab
) -> ColumnElement[bool]:
    """一级入口 → 可见范围。**只负责可见范围，`state` 由调用方拼。**

    草稿与回收站不在这里：它们是 `state`，与入口正交（设计 §0）。
    """
    if tab is LibraryTab.PERSONAL:
        return and_(
            LibraryAsset.owner_id == actor.id,
            LibraryAsset.visibility == "personal",
        )
    if tab is LibraryTab.TEAM:
        return LibraryAsset.visibility == "team"
    if tab is LibraryTab.GROUP:
        return and_(
            LibraryAsset.visibility == "group",
            LibraryAsset.id.in_(
                select(LibraryAssetGroup.asset_id).where(
                    LibraryAssetGroup.group_id.in_(list(groups))
                )
            ),
        )
    return active_visible_clause(actor, groups)


def can_read(
    asset: LibraryAsset,
    actor: CurrentUser,
    my_groups: Sequence[str],
    asset_groups: Sequence[str] = (),
) -> bool:
    """单条读取（详情 / 取件 / 下载）。

    与 `active_visible_clause` 必须同口径，但这里是 Python 分支：数据已经在手里，
    再拼一段 SQL 去问数据库同一个问题是浪费。两处改一处就漂——名字取成
    `can_read` 而不是 `is_visible`，是为了在调用点读起来就是「读判定」。

    `asset_groups` 必须由调用方给出（`library_asset_groups` 里的行）：
    「我在某个组里」不等于「这条资产共享到了我的组」，少了这个参数就会把
    别人组里的素材判成可见——那是本文件最危险的一种偷懒。
    """
    if asset.state == "trash":
        return asset.owner_id == actor.id or actor.has(PERM_DELETE_ANY)
    if asset.state == "draft":
        return asset.owner_id == actor.id or actor.has(PERM_DRAFT_REVIEW)
    if asset.owner_id == actor.id:
        # 共享出去之后仍是「我的」，被移出小组也不该看不见自己的东西
        return True
    if asset.visibility == "team":
        return True
    if asset.visibility == "group":
        return bool(set(asset_groups) & set(my_groups))
    return False


async def asset_group_ids(
    session: AsyncSession, asset_ids: Sequence[str]
) -> Mapping[str, list[str]]:
    """一批资产分别共享到了哪些组。列表页一次查完，不按行循环。"""
    wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if not wanted:
        return {}
    rows = await session.execute(
        select(LibraryAssetGroup.asset_id, LibraryAssetGroup.group_id).where(
            LibraryAssetGroup.asset_id.in_(wanted)
        )
    )
    result: dict[str, list[str]] = {}
    for asset_id, group_id in rows.all():
        result.setdefault(asset_id, []).append(group_id)
    return result


async def leader_group_ids(session: AsyncSession, user_id: str) -> list[str]:
    """我是哪些组的负责人。存在 v2 自己的表里，不改身份模块的组模型。"""
    return list(
        await session.scalars(
            select(LibraryGroupLeader.group_id).where(
                LibraryGroupLeader.user_id == user_id
            )
        )
    )


async def is_group_leader(session: AsyncSession, user_id: str, group_id: str) -> bool:
    if not group_id:
        return False
    return (
        await session.scalar(
            select(LibraryGroupLeader.id).where(
                LibraryGroupLeader.user_id == user_id,
                LibraryGroupLeader.group_id == group_id,
            )
        )
    ) is not None


async def group_names_for(
    session: AsyncSession, group_ids: Sequence[str]
) -> dict[str, str]:
    return await group_names(session, group_ids)


async def owner_names(
    session: AsyncSession, owner_ids: Sequence[str]
) -> dict[str, str]:
    return await user_names(session, owner_ids)


async def load_asset(session: AsyncSession, asset_id: str) -> LibraryAsset:
    """按 id 取行，不存在就 404。

    读判定不在这里：调用方自己决定要不要 `load_visible`。删除与审批流程需要
    「拿得到行、再按规则拒绝」，而读取只需要「看不见就当不存在」——两者的
    错误语义不同，合并成一个函数会逼其中一个说谎。
    """
    row = await session.get(LibraryAsset, asset_id)
    if row is None:
        raise NotFound("资产不存在")
    return row


async def load_visible(
    session: AsyncSession, asset_id: str, actor: CurrentUser
) -> LibraryAsset:
    """可见即可读：看不见的 id 一律 404，不区分「不存在」与「没权限」。"""
    row = await session.get(LibraryAsset, asset_id)
    if row is None:
        raise NotFound("资产不存在")
    my_groups = await all_groups(session, actor)
    rows = await asset_group_ids(session, [asset_id])
    if not can_read(row, actor, my_groups, rows.get(asset_id, [])):
        raise NotFound("资产不存在")
    return row
