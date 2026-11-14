"""标签：两级词表 CRUD + 资产打标。

## 两级口径（2026-11-11）

`parent_id == ""` 是一级，非空是它旗下的二级，**固定两级、禁止三级**。
一级是「分组」，二级是真正能打在素材上的词；但有子级的一级不再是叶子，
所以打标守卫拦的是「**有子标签的标签**」而不是「所有一级」——否则存量里
「音乐」「AI生成」这些孤立的一级会一夜之间变成看得见却打不上的死词。

由此带来三个必须同一套口径的地方，错一处就会出现「标签旁写 8、点进去只有 3 条」：

- **打标**：`_ensure_tags_exist(reject_groups=True)` 拒掉有子级的分组标签；
  系统内部的合并（封装继承成员标签、生成入库）不拦，见各调用点注释。
- **筛选**：`expand_tag_ids` 把选中的一级展开成「自身 + 直属子级」，仍是 OR。
- **计数**：一级 = 自身 + 旗下二级，按**资产 id 去重**合计。

## 三个刻意的取舍

1. **新建同名标签不报错，返回已有的那条**。「同名」现在指**同一父下**同名：
   两个系列都有「小红书」是合理的，同一个系列里有两个才该合并成一条。
2. **改名撞到已有名字时报 409，不做隐式合并**。改名与合并是两个不可逆程度不同的
   动作：改名只换显示，合并会把两条标签的资产并到一起、之后拆不开。
3. **删除一级时把子级提升为一级**，不级联删、也不删资产绑定。标签是组织手段，
   删一个分组不该把下面的词和别人打的标一起带走。

标签本身不做权限隔离（全局词表），`library:tag:manage` 管的是「谁能改词表」。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from modules.library import access
from modules.library.models import LibraryAsset, LibraryAssetTag, LibraryTag
from modules.library.schema import (
    LibraryTab,
    LibraryTagOut,
    LibraryTagStatOut,
    TagIn,
    TagParentIn,
)

__all__ = [
    "TagService",
    "add_asset_tags",
    "expand_tag_ids",
    "remove_asset_tags",
    "set_asset_tags",
    "sync_pack_tags",
    "tag_ids_by_asset",
    "tag_ids_of_assets",
]


def to_tag_out(row: LibraryTag, parent_name: str | None = None) -> LibraryTagOut:
    """库里用空串表示一级，出参统一转成 `null`（前端不用知道哨兵串）。"""
    return LibraryTagOut(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id or None,
        parent_name=parent_name,
        depth=2 if row.parent_id else 1,
    )


async def expand_tag_ids(session: AsyncSession, tag_ids: Sequence[str]) -> list[str]:
    """把选中的标签展开成「自身 + 直属子级」，供列表 / 选择器筛选。

    固定两级所以一次查询就够，不用递归 CTE。选中一级时把它旗下的二级一起
    放进 `IN (...)`；选中二级时保持原样（二级不会再有子级）。
    资产侧本来就是 `asset_id IN (...)`，展开后天然按资产去重，不会重复计数。
    """
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return []
    children = (
        await session.scalars(
            select(LibraryTag.id).where(LibraryTag.parent_id.in_(wanted))
        )
    ).all()
    return list(dict.fromkeys([*wanted, *children]))


async def _ensure_tags_exist(
    session: AsyncSession, tag_ids: Sequence[str], *, reject_groups: bool = False
) -> None:
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return
    found = set(
        await session.scalars(select(LibraryTag.id).where(LibraryTag.id.in_(wanted)))
    )
    missing = [tag_id for tag_id in wanted if tag_id not in found]
    if missing:
        # 400 而不是 404：一次请求里可能同时含存在与不存在的 id，
        # 404 会让人以为「整个打标失败了」，而实际只是其中几个不合法。
        raise BadRequest(f"标签不存在：{', '.join(missing[:5])}")
    if reject_groups:
        # 「有子标签」= 它的 id 出现在别人的 parent_id 里。两级模型下这就够了，
        # 也顺带拦住「万一有人手工塞了一条三级」的脏数据。
        group_ids = set(
            (
                await session.scalars(
                    select(LibraryTag.parent_id)
                    .where(LibraryTag.parent_id.in_(wanted))
                    .distinct()
                )
            ).all()
        )
        blocked = [tag_id for tag_id in wanted if tag_id in group_ids]
        if blocked:
            raise BadRequest("一级标签只做分组，请选择它下面的二级标签")



async def tag_ids_by_asset(
    session: AsyncSession, asset_ids: Sequence[str]
) -> dict[str, list[str]]:
    wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if not wanted:
        return {}
    rows = await session.execute(
        select(LibraryAssetTag.asset_id, LibraryAssetTag.tag_id).where(
            LibraryAssetTag.asset_id.in_(wanted)
        )
    )
    result: dict[str, list[str]] = {}
    for asset_id, tag_id in rows.all():
        result.setdefault(asset_id, []).append(tag_id)
    return result


async def tag_ids_of_assets(
    session: AsyncSession, asset_ids: Sequence[str]
) -> list[str]:
    """一批资产的标签 id 并集（去重、按 id 稳定排序）。

    与 `tag_ids_by_asset` 的区别是这里**不按资产分组**：封装要的是「里面所有素材
    一共有哪些标签」，把分组结果再拍平一遍不如直接查一次 `distinct`。
    """
    wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if not wanted:
        return []
    rows = await session.scalars(
        select(LibraryAssetTag.tag_id)
        .where(LibraryAssetTag.asset_id.in_(wanted))
        .distinct()
    )
    return sorted({tag_id for tag_id in rows.all()})


async def sync_pack_tags(
    session: AsyncSession,
    pack_id: str,
    member_ids: Sequence[str],
    *,
    base: Sequence[str] | None = None,
    dry_run: bool = False,
) -> int:
    """封装**自动继承成员标签**，返回新补上的标签数。

    ## 口径（写时合并，不做实时联动）

    **不再自动打「封装」标签**（2026-11-14）：那是「这是个封装」这件事的**状态**，
    已经由 `library_assets.kind = "pack"` 表达，而类型筛选现在有「封装」这一档。
    写成标签的代价很具体：标签是用户可编辑的词表，卡片上的 × 一点就没了，之后这个
    封装在任何筛选项里都找不到（只剩「全部」）。状态不能用可编辑词表表达。

    - `base` 给了就是「整体替换」的基准（建封装时用：用户手选的标签）；
      没给就读出封装当前标签，在它上面**只增不减**。
    - 两种情形都会再并上成员标签的并集。所以封装的标签是真数据、能像普通素材
      一样被 × 去掉或 + 加上；不做「读的时候临时拼成员标签」那一套——那样
      卡片上的 × 就成了摆设，按标签筛选也得另写一套 SQL。
    - **只在封装创建 / 替换成员时合并**。成员素材后来单独打标不会回写所有包含
      它的封装：那要反查引用关系、还会把用户刚从封装上删掉的标签又塞回来。
      代价是「只增不减」——重新编辑一次成员会把仍属于成员的标签补回来，
      这与「封装自动获得成员标签」的定义一致，写在这里免得下次被当成 bug。

    `dry_run` 给存量回填命令用：只算差、不写库。
    """
    current = (
        list(base)
        if base is not None
        else (await tag_ids_by_asset(session, [pack_id])).get(pack_id, [])
    )
    inherited = await tag_ids_of_assets(session, member_ids)
    wanted = list(dict.fromkeys([*current, *inherited]))
    added = len([tag_id for tag_id in inherited if tag_id not in current])
    if not dry_run and (base is not None or added):
        # 合并的是「成员已有的绑定」，不是用户在界面上打标。
        # 某个成员在两级改造前挂过一级标签、之后那个一级成了分组，不能因此
        # 让整个封装创建失败 —— 系统内部合并不做分组校验。
        await set_asset_tags(session, pack_id, wanted, reject_groups=False)
    return added


async def add_asset_tags(
    session: AsyncSession, asset_id: str, tag_ids: Sequence[str]
) -> None:
    """幂等追加。已存在的绑定跳过——批量打标时同一批里重复点一次很常见。"""
    await _ensure_tags_exist(session, tag_ids, reject_groups=True)
    current = set(
        await session.scalars(
            select(LibraryAssetTag.tag_id).where(LibraryAssetTag.asset_id == asset_id)
        )
    )
    for tag_id in dict.fromkeys(tag_ids):
        if tag_id and tag_id not in current:
            session.add(LibraryAssetTag(asset_id=asset_id, tag_id=tag_id))


async def remove_asset_tags(
    session: AsyncSession, asset_id: str, tag_ids: Sequence[str]
) -> None:
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return
    await session.execute(
        delete(LibraryAssetTag).where(
            LibraryAssetTag.asset_id == asset_id,
            LibraryAssetTag.tag_id.in_(wanted),
        )
    )


async def set_asset_tags(
    session: AsyncSession,
    asset_id: str,
    tag_ids: Sequence[str],
    *,
    reject_groups: bool = True,
) -> None:
    """整体替换（PATCH 的语义）。空数组 = 清空标签。

    `reject_groups=False` 只给系统内部合并用（封装继承、生成入库），见
    `sync_pack_tags` 里的注释；界面上的打标一律走默认值。
    """
    await _ensure_tags_exist(session, tag_ids, reject_groups=reject_groups)
    kept = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if kept:
        await session.execute(
            delete(LibraryAssetTag).where(
                LibraryAssetTag.asset_id == asset_id,
                LibraryAssetTag.tag_id.not_in(kept),
            )
        )
    else:
        await session.execute(
            delete(LibraryAssetTag).where(LibraryAssetTag.asset_id == asset_id)
        )
    current = set(
        await session.scalars(
            select(LibraryAssetTag.tag_id).where(LibraryAssetTag.asset_id == asset_id)
        )
    )
    for tag_id in kept:
        if tag_id not in current:
            session.add(LibraryAssetTag(asset_id=asset_id, tag_id=tag_id))


class TagService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tags(
        self,
        actor: CurrentUser,
        *,
        state: str = "active",
        library: LibraryTab = LibraryTab.ALL,
        kind: str = "",
    ) -> list[LibraryTagStatOut]:
        """标签栏的数据：全部标签 + 当前入口下各标签的素材数。

        两个口径刻意与 `LibraryService.list_assets` 一致，否则「标签旁写 8，
        点进去只有 3 条」会让人以为数据丢了：

        - `state=active` 带上 `tab_clause` 与 `hidden_by_pack_clause`（被装进
          封装的成员在列表里不单列，计数里也不该算）；
        - 草稿 / 回收站按权限点收窄到本人或全量，忽略 `library`。

        **`kind` 与列表的类别筛选同口径**：悬浮栏按类别看库时，胶囊上的数字
        必须也是「这一类里有几条」。不按类别算的话，用户在「图片」下看到
        胶囊写 9、点进去只有 4 条——数字说的是全库，筛的是本类。

        **不受当前标签筛选影响**：计数是「这个入口里有多少素材属于该标签」，
        不是「再叠上已选标签后还剩多少」；否则每点一次筛选，其余标签的数字
        都会跳一遍，反而没法读。关键词筛选同理不计入。

        排序按 `created_at` 升序（先加的在前），`id` 收尾保证同秒创建时稳定。
        """
        groups = await access.all_groups(self.session, actor)
        rows = (
            await self.session.scalars(
                select(LibraryTag).order_by(LibraryTag.created_at, LibraryTag.id)
            )
        ).all()
        names = {row.id: row.name for row in rows}
        children: dict[str, list[str]] = {}
        for row in rows:
            if row.parent_id:
                children.setdefault(row.parent_id, []).append(row.id)

        per_tag = await self._asset_ids_by_tag(actor, groups, state, library, kind)
        # 一级标签的数字 = 自身 + 旗下二级，按资产 id 去重。这与「点一级展开到
        # 子级」的筛选口径完全一致；`count(distinct)` 做不到跨标签的并集去重，
        # 所以这里把绑定行取回来在内存里归并（当前入口的可见集本来就不大）。
        counts: dict[str, int] = {}
        for row in rows:
            asset_ids: set[str] = set()
            for tag_id in [row.id, *children.get(row.id, [])]:
                asset_ids.update(per_tag.get(tag_id, ()))
            counts[row.id] = len(asset_ids)

        return [
            LibraryTagStatOut(
                id=row.id,
                name=row.name,
                parent_id=row.parent_id or None,
                parent_name=names.get(row.parent_id) if row.parent_id else None,
                depth=2 if row.parent_id else 1,
                child_count=len(children.get(row.id, [])),
                asset_count=counts[row.id],
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def _asset_ids_by_tag(
        self,
        actor: CurrentUser,
        groups: list[str],
        state: str,
        library: LibraryTab,
        kind: str = "",
    ) -> dict[str, set[str]]:
        """当前入口下「每个标签 → 可见资产 id 集合」，一次查询取回。

        可见条件与 `LibraryService.list_assets` 一字不差；比旧的 `group by count`
        多取一列 asset_id，是为了给一级标签做「自身 ∪ 子级」的资产级去重。

        `kind` 是类别筛选（image / video / audio / text），与列表的类别口径
        一致；留空表示不按类别收窄。
        """
        query = (
            select(LibraryAssetTag.tag_id, LibraryAssetTag.asset_id)
            .join(LibraryAsset, LibraryAsset.id == LibraryAssetTag.asset_id)
            .where(LibraryAsset.state == state)
        )
        if kind:
            query = query.where(LibraryAsset.kind == kind)
        if state == "active":
            query = query.where(
                LibraryAsset.deleted_at.is_(None),
                access.tab_clause(actor, groups, library),
                access.hidden_by_pack_clause(actor, groups, library),
            )
        elif state == "draft":
            if not actor.has(access.PERM_DRAFT_REVIEW):
                query = query.where(LibraryAsset.owner_id == actor.id)
        elif not actor.has(access.PERM_DELETE_ANY):
            query = query.where(LibraryAsset.owner_id == actor.id)

        result = await self.session.execute(query)
        grouped: dict[str, set[str]] = {}
        for tag_id, asset_id in result.all():
            grouped.setdefault(tag_id, set()).add(asset_id)
        return grouped

    async def _parent_name(self, row: LibraryTag) -> str | None:
        if not row.parent_id:
            return None
        parent = await self.session.get(LibraryTag, row.parent_id)
        return parent.name if parent is not None else None

    async def create(self, payload: TagIn, actor: CurrentUser) -> LibraryTagOut:
        """同名幂等的口径收窄到「同一父下」：两个系列各自有「小红书」是合法的。"""
        name = payload.name.strip()
        parent_id = payload.parent_id or ""
        parent_name: str | None = None
        if parent_id:
            parent = await self.session.get(LibraryTag, parent_id)
            if parent is None:
                raise BadRequest("父标签不存在")
            if parent.parent_id:
                # 固定两级：挂在二级下面就是三级，不引递归 CTE
                raise BadRequest("只能建两级标签，父标签本身不能是二级")
            parent_name = parent.name
        existing = await self.session.scalar(
            select(LibraryTag).where(
                LibraryTag.name == name, LibraryTag.parent_id == parent_id
            )
        )
        if existing is not None:
            return to_tag_out(existing, parent_name)
        row = LibraryTag(name=name, parent_id=parent_id, created_by=actor.id)
        self.session.add(row)
        await self.session.commit()
        return to_tag_out(row, parent_name)

    async def rename(self, tag_id: str, payload: TagIn) -> LibraryTagOut:
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        parent_name = await self._parent_name(row)
        name = payload.name.strip()
        if name == row.name:
            return to_tag_out(row, parent_name)
        # 改名不动层级：撞名只在**同一个父**下判定，跨一级同名不冲突
        other = await self.session.scalar(
            select(LibraryTag).where(
                LibraryTag.name == name,
                LibraryTag.parent_id == row.parent_id,
                LibraryTag.id != row.id,
            )
        )
        if other is not None:
            raise Conflict("同一分组下已有同名标签")
        row.name = name
        await self.session.commit()
        return to_tag_out(row, parent_name)

    async def move(self, tag_id: str, payload: TagParentIn) -> LibraryTagOut:
        """改层级：`parentId=null` 提升为一级，指向一级则降为二级。

        三道守卫：不能挂到自己下面；父必须是一级（禁止三级）；自己旗下还有
        子标签时不能降为二级（否则那些子标签会变成三级）。「指向后代」在
        「父必须是一级」这条里已经被排除，不需要再写递归查后代。
        """
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        parent_id = payload.parent_id or ""
        if parent_id == row.id:
            raise BadRequest("标签不能挂到自己下面")
        parent_name: str | None = None
        if parent_id:
            parent = await self.session.get(LibraryTag, parent_id)
            if parent is None:
                raise BadRequest("父标签不存在")
            if parent.parent_id:
                raise BadRequest("只能建两级标签，不能挂在二级下面")
            parent_name = parent.name
            child_count = await self.session.scalar(
                select(func.count())
                .select_from(LibraryTag)
                .where(LibraryTag.parent_id == row.id)
            )
            if child_count:
                raise BadRequest("它旗下还有二级标签，先把子标签移走")
        clash = await self.session.scalar(
            select(LibraryTag.id).where(
                LibraryTag.name == row.name,
                LibraryTag.parent_id == parent_id,
                LibraryTag.id != row.id,
            )
        )
        if clash is not None:
            raise Conflict("目标分组下已有同名标签，请先改名")
        row.parent_id = parent_id
        await self.session.commit()
        return to_tag_out(row, parent_name)

    async def delete(self, tag_id: str) -> None:
        """删标签同时清绑定；**旗下二级提升为一级**，不级联删。

        不做「还有资产在用就拒绝」：标签是组织手段，不是资产的一部分，
        删一个词表项不该被 200 条资产卡住。资产本身完好无损。
        """
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        children = (
            await self.session.scalars(
                select(LibraryTag).where(LibraryTag.parent_id == row.id)
            )
        ).all()
        if children:
            top_names = set(
                (
                    await self.session.scalars(
                        select(LibraryTag.name).where(
                            LibraryTag.parent_id == "", LibraryTag.id != row.id
                        )
                    )
                ).all()
            )
            clash = [child.name for child in children if child.name in top_names]
            if clash:
                # 提升后 `(name, parent_id)` 会撞唯一索引；与其报 500，
                # 不如把「先给子标签改个名」这件事说清楚。
                raise Conflict(f"子标签与已有的一级同名，请先改名：{', '.join(clash[:5])}")
            await self.session.execute(
                update(LibraryTag)
                .where(LibraryTag.parent_id == row.id)
                .values(parent_id="")
            )
        await self.session.execute(
            delete(LibraryAssetTag).where(LibraryAssetTag.tag_id == tag_id)
        )
        await self.session.delete(row)
        await self.session.commit()
