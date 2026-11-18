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

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotFound
from core.ports.org_directory import group_ids_of, group_names, user_names
from modules.library.models import (
    LibraryAsset,
    LibraryAssetGroup,
    LibraryGroupLeader,
)
from modules.library.schema import LibraryTab

__all__ = [
    "PURPOSE_CASE",
    "active_visible_clause",
    "all_groups",
    "asset_group_ids",
    "can_read",
    "group_names_for",
    "is_group_leader",
    "leader_group_ids",
    "load_asset",
    "load_visible",
    "owner_clause",
    "owner_names",
    "readable_rows",
    "tab_clause",
]

#: 草稿箱代审范围：持这个码的人能看到别人的草稿（否则只看自己的）。
PERM_DRAFT_REVIEW = "library:draft:review"
#: 回收站与团队库的兜底通道：删别人的、删团队库的、彻底删除。
PERM_DELETE_ANY = "library:delete:any"

#: 资产用途：`purpose = CASE` 表示「模板库的展示案例」。空串是普通素材。
#: 用常量而不是散落的字符串字面量：这个值同时出现在 SQL 分支（`active_visible_clause`）、
#: 服务层过滤（`exclude_purposes`）和迁移里，拼错一处就是「案例既不在池里也不可读」。
PURPOSE_CASE = "case"


async def all_groups(session: AsyncSession, actor: CurrentUser) -> list[str]:
    """我所属的小组。走组织目录注入口，未接上时为空（小组库为空，不多看见）。"""
    return await group_ids_of(session, actor.id)


def owner_clause(
    actor: CurrentUser, group_branch: ColumnElement[bool]
) -> ColumnElement[bool]:
    """owner 分支：**本人对自己的素材一律可见**（与 `can_read` 同口径）。

    但「共享出去之后不在个人库重复出现」这条界面口径要保住：素材共享到某个
    小组后，它出现在小组库里，个人库不再重复显示——否则同一张图会在两个入口
    各出现一次，用户会以为是两份。

    两条合起来的口径就是「本人可见，但只在**别处看不到**的时候回个人库」：

    - 共享到小组 G、我还在 G 里 → 小组库能看到（不必在个人库重复）；
    - 共享到小组 G、我已被移出 G → 小组库看不到（必须回个人库，否则这条素材
      在四个入口里全部消失，只剩直链能打开）。

    早先这里写的是 `visibility == 'personal'`，而同文件的 `can_read`（owner
    分支无条件放行）不是这么判的：SQL 少给一支、Python 多给一支，于是「本人
    被移出小组后素材在界面上彻底消失，直链却能打开」。这个文件存在的理由
    就是「同一条规则不能写第二遍」，所以两处必须一起改。
    """
    return and_(
        LibraryAsset.owner_id == actor.id,
        or_(
            LibraryAsset.visibility == "personal",
            and_(
                LibraryAsset.visibility == "group",
                not_(group_branch),
            ),
        ),
    )


def active_visible_clause(
    actor: CurrentUser, groups: Sequence[str]
) -> ColumnElement[bool]:
    """`state=active` 前提下「我能看到」的 SQL 条件。

    四支并集：我自己的、共享到我所在小组的、共享给团队的、以及案例。
    「我自己的」的口径见 `owner_clause`：共享出去且我还在那个组里时归小组库，
    不在个人库重复出现；我已被移出那个组时回个人库——否则自己的素材会从
    四个入口里全部消失。

    第四支是**案例**（`purpose='case'`）：模板卡片对所有登录用户开放，卡片上的
    封面与悬停播放都要读得到这条素材。它不走 `visibility` —— 案例既不属于某个
    人的个人库，也不该出现在团队库里；「谁能浏览案例池」是另一道门（见
    `router` 的 `purpose=case` 入口，要 `library:case:read`），与「谁能读这一条」
    是两件事。列表默认排除案例（`LibraryService.list` 的 `exclude_purposes`），
    所以这一支不会把案例灌进「全部 / 个人 / 团队」。
    """
    group_branch = LibraryAsset.id.in_(
        select(LibraryAssetGroup.asset_id).where(
            LibraryAssetGroup.group_id.in_(list(groups))
        )
    )
    return or_(
        owner_clause(actor, group_branch),
        and_(LibraryAsset.visibility == "group", group_branch),
        LibraryAsset.visibility == "team",
        LibraryAsset.purpose == PURPOSE_CASE,
    )


def tab_clause(
    actor: CurrentUser,
    groups: Sequence[str],
    tab: LibraryTab,
) -> ColumnElement[bool]:
    """一级入口 → 可见范围。**只负责可见范围，`state` 由调用方拼。**

    草稿与回收站不在这里：它们是 `state`，与入口正交（设计 §0）。
    """
    if tab is LibraryTab.PERSONAL:
        # 与 `active_visible_clause` 同一支（`owner_clause`）：共享出去又
        # 被移出小组的素材要在这里出现，否则自己的东西在界面上彻底消失。
        return owner_clause(
            actor,
            LibraryAsset.id.in_(
                select(LibraryAssetGroup.asset_id).where(
                    LibraryAssetGroup.group_id.in_(list(groups))
                )
            ),
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

    **这里不含「读得到」的其它来源**：本函数是纯 Python 的判定，只看这一行
    自己的字段与所在小组。读入口统一走 `load_visible` / `readable_rows`，
    写路径（批量 `_require_writable`）继续只认本函数——
    「看得到」不等于「能改」。
    """
    if asset.state == "trash":
        return asset.owner_id == actor.id or actor.has(PERM_DELETE_ANY)
    if asset.state == "draft":
        return asset.owner_id == actor.id or actor.has(PERM_DRAFT_REVIEW)
    if asset.owner_id == actor.id:
        # 共享出去之后仍是「我的」，被移出小组也不该看不见自己的东西
        return True
    # 案例：任何登录用户都能读（模板卡片要显示、要播）。这条**刻意放在
    # `visibility` 之前**——案例的可见性不由它决定，它也不该因为 owner 把它
    # 设成 personal 就对外消失。草稿与回收站已在上面两行挡掉了。
    if asset.purpose == "case":
        return True
    if asset.visibility == "team":
        return True
    if asset.visibility == "group":
        return bool(set(asset_groups) & set(my_groups))
    return False


async def readable_rows(
    session: AsyncSession, asset_ids: Sequence[str], actor: CurrentUser
) -> dict[str, LibraryAsset]:
    """这批 id 里，`actor` **现在**还能读的行。

    存在的理由是异步任务：批量下载建任务时判过一次可读性，真正打包可能几分钟后，
    人可能被移出小组、共享可能被取消。只在建任务时判，等于把当时的权限
    冻结进任务里——人已经失权，包照样生成、照样能取件（取件只校验 owner）。

    判据必须与 `DownloadService._collect` 同口径，所以放在这里共用：
    `can_read` 一家说了算。各自写一份必然漂移，
    而漂移的方向恰好是「多给权限」，不会有人报错。
    """
    wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if not wanted:
        return {}
    rows = {
        row.id: row
        for row in (
            await session.scalars(select(LibraryAsset).where(LibraryAsset.id.in_(wanted)))
        ).all()
    }
    if not rows:
        return {}
    my_groups = await all_groups(session, actor)
    group_map = await asset_group_ids(session, list(rows))
    return {
        asset_id: row
        for asset_id, row in rows.items()
        if can_read(row, actor, my_groups, group_map.get(asset_id, []))
    }


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


async def load_asset_for_update(session: AsyncSession, asset_id: str) -> LibraryAsset:
    """按 id 取行，并**锁住这一行**（`SELECT ... FOR UPDATE`）。

    ## 为什么需要锁

    审批的「撤销某组共享」是三步：删组行 → 数剩余组行 → 写 `visibility`。
    两个审批并发时，各自看到的快照可能都停留在对方删除之前，最后落库的是
    `visibility=group` 而组行已经空了 —— 那条资产从此**既删不掉**
    （`visibility=group` 走共享分支，要先撤销共享）**也撤销不了**（组行没了，
    没组可撤）。这个状态没有任何代码路径能走出来，只能手工改库。

    锁把「同一资产上的审批」串行化：第二个审批要等第一个提交后才能拿到行，
    那时它数到的组行就是最新的。锁的是**资产行**而不是申请单 —— 申请单各自
    不同，锁它挡不住两个不同申请的审批撞在一起。

    SQLite 没有行锁，但那个方言本来就单写者串行，SQLAlchemy 会静默忽略这个
    子句（不是报错）。真正需要它的是 PG。
    """
    row = await session.scalar(
        select(LibraryAsset).where(LibraryAsset.id == asset_id).with_for_update()
    )
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
    if can_read(row, actor, my_groups, rows.get(asset_id, [])):
        return row
    raise NotFound("资产不存在")
