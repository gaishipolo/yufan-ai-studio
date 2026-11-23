"""标签：**两级**词表（维度 / 取值）+ 资产打标。

## 两级口径（2026-11-17 起）

`parent_id == ""` 是一级，往下是二级，**最多两级**。

- **一级 = 维度**（产品 / 画面类型 / 角色 / 场景）：是分组，**不可打标**。
- **二级 = 取值**（6036 / T35 / 正面图 / 菜菜）：是真正能打在素材上的词。

这是「两个正交维度」的最小表达。之前是三级（维度 / 系列 / 具体词），一级恒为
分组、二级恒为系列、三级才是词 —— 结果「三视图 / 正面图 / 细节图」这种
**跨产品复用**的词只能挂在某个具体产品下面，每上一个新产品就要把整套词复制一遍
（3N 个标签）。两级之后「正面图」只存在于「画面类型」维度下，所有产品共用。

## 筛选语义：组内 OR，组间 AND

这是两级带来的**唯一一处语义重定义**，也是整个改动的核心：

- 选中的标签按**一级维度**分组；
- **同一维度内多选是 OR**（选 6036 与 T35 = 两个产品都要看）；
- **不同维度之间是 AND**（选「正面图」+「6036」= 既是 6036 又是正面图）。

之前是全 OR（所有选中标签展开成「自身 + 全部下级」再 `IN` 一次）。在三级下这
说得通（那是三条并列的分类轴）；两级之后它是错的 ——「正面图」与「6036」是两条
**互相独立**的轴，并集会把「6036 的三视图」也带出来，而用户要的是交集。

一个素材可以同时属于多个维度（`产品/6036` + `画面类型/正面图`），这正是
AND 语义成立的前提 —— 一个素材在每个维度里只取一个值。

## 其余三处口径

- **打标**：`_ensure_tags_exist(reject_groups=True)` 拒掉有子级的分组标签；
  系统内部的合并（生成入库）不拦，见各调用点注释。这条与层数无关。
- **筛选**：`expand_tag_ids` 展开成「自身 + 全部下级」，再由
  `tag_filter_clause` 按维度组装成「组内 OR、组间 AND」。
- **计数**：一个标签 = 自身 + 全部下级，按**资产 id 去重**合计。

## 三个刻意的取舍

1. **新建同名标签不报错，返回已有的那条**。「同名」现在指**同一父下**同名：
   两个系列都有「小红书」是合理的，同一个系列里有两个才该合并成一条。
2. **改名撞到已有名字时报 409，不做隐式合并**。改名与合并是两个不可逆程度不同的
   动作：改名只换显示，合并会把两条标签的资产并到一起、之后拆不开。
3. **删除标签时把子级提升一级**，不级联删、也不删资产绑定。标签是组织手段，
   删一个分组不该把下面的词和别人打的标一起带走。

标签本身不做权限隔离（全局词表），`library:tag:manage` 管的是「谁能改词表」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import and_, delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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
    "assignable_tag_ids",
    "expand_tag_ids",
    "group_tag_ids_by_dimension",
    "remove_asset_tags",
    "set_asset_tags",
    "tag_filter_clause",
    "tag_ids_by_asset",
]


#: 标签树最多两级。一级是维度（分组，不可打标），二级是取值（可打标）。
#:
#: 两级不是「偷懒砍一层」，而是**维度模型**的最小表达：素材的多个属性维度
#: （产品、画面类型、角色…）彼此正交，每条轴自己是一棵两级小树。
#: 三级会把「跨轴复用的词」（正面图/三视图/细节图）塞进某个具体轴的深处，
#: 于是每新增一个实例（产品）就要把一整套词复制一遍。
MAX_TAG_DEPTH = 2


def tag_tree_index(
    rows: Sequence[LibraryTag],
) -> tuple[dict[str, str], dict[str, int], dict[str, str]]:
    """(父映射, 层级, 祖先路径)。

    词表是全局的几十条，一次全取回来在内存里算比递归查询省事 —— 筛选与计数
    每次都要用这个口径，来回查库反而更慢。

    路径给的是**含自己**的整条链：一级 =「产品」，二级 =「产品 / 6030」。
    卡片上要显示整条链，只给一层父名是不够的。
    """
    parent = {row.id: (row.parent_id or "") for row in rows}
    names = {row.id: row.name for row in rows}
    depth: dict[str, int] = {}
    path: dict[str, str] = {}
    for tag_id in parent:
        chain: list[str] = []
        current = tag_id
        seen: set[str] = set()
        while current in parent and current not in seen:
            seen.add(current)
            chain.append(current)
            current = parent[current]
            if len(chain) >= MAX_TAG_DEPTH:
                break
        depth[tag_id] = len(chain)
        path[tag_id] = " / ".join(reversed([names.get(item, "") for item in chain]))
    return parent, depth, path


def _children_of(rows: Sequence[LibraryTag]) -> dict[str, list[str]]:
    """父 id → 直接子 id 列表。"""
    children: dict[str, list[str]] = {}
    for row in rows:
        if row.parent_id:
            children.setdefault(row.parent_id, []).append(row.id)
    return children


def _descendants(tag_id: str, children: dict[str, list[str]]) -> list[str]:
    """自身 + 全部下级。层级只有三档，展开就是几层循环。"""
    out = [tag_id]
    pending = [tag_id]
    while pending:
        current = pending.pop()
        for child in children.get(current, ()):
            out.append(child)
            pending.append(child)
    return out


async def all_tags(session: AsyncSession) -> list[LibraryTag]:
    return list(
        await session.scalars(
            select(LibraryTag).order_by(LibraryTag.created_at, LibraryTag.id)
        )
    )


def to_tag_out(
    row: LibraryTag, parent_name: str | None = None, depth: int = 1
) -> LibraryTagOut:
    """库里用空串表示一级，出参统一转成 `null`（前端不用知道哨兵串）。"""
    return LibraryTagOut(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id or None,
        parent_name=parent_name,
        depth=depth,
    )


async def expand_tag_ids(session: AsyncSession, tag_ids: Sequence[str]) -> list[str]:
    """把选中的标签展开成「自身 + 全部下级」。

    两级树里下级只有一层，所以展开最多多一个 id。展开**不决定组合方式** ——
    组内 OR、组间 AND 由 `tag_filter_clause` 组装（见那里的说明）。
    """
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return []
    children = _children_of(await all_tags(session))
    expanded: set[str] = set()
    for tag_id in wanted:
        expanded.update(_descendants(tag_id, children))
    return [*wanted, *[tag_id for tag_id in expanded if tag_id not in wanted]]


def group_tag_ids_by_dimension(
    tag_ids: Sequence[str], parent_of: Mapping[str, str]
) -> list[list[str]]:
    """把选中的标签按**一级维度**分组。

    一级标签本身归到它自己那组（选中「产品」= 想看产品维度下的全部取值）。
    没有 parent 记录的（脏数据 / 已被删的父）单独归一组，不与别的混 ——
    混进哪组都会算错交集。

    返回的每组是「该维度内要看的标签 id」。
    """
    groups: dict[str, list[str]] = {}
    for tag_id in dict.fromkeys(tag_ids):
        if not tag_id:
            continue
        parent = parent_of.get(tag_id, "")
        # 选中的就是一级 → 它自己那组；否则按它的一级祖先归组。
        key = tag_id if parent == "" else parent
        groups.setdefault(key, []).append(tag_id)
    return list(groups.values())


def tag_filter_clause(
    tag_rows: Sequence[LibraryTag], tag_ids: Sequence[str]
) -> ColumnElement[bool] | None:
    """标签筛选的 SQL 条件：**组内 OR，组间 AND**。

    两级维度模型下，选中的标签分属不同的一级维度（产品、画面类型、角色…），
    每条轴独立：

    - 同一维度内多选 = OR（选 6036 与 T35 = 两个产品都要看）；
    - 不同维度之间 = AND（选「正面图」+「6036」= 既是 6036 又是正面图）。

    **不能沿用以前的全 OR**：那是三级下的语义（几条并列的分类轴）。
    两级之后它是错的 ——「正面图」与「6036」互相独立，并集会把
    「6036 的三视图」也带出来，而用户要的是交集。

    实现上就是「每个维度一个 `EXISTS` 子句，AND 起来」：一个资产必须**同时**
    命中每个维度的条件。维度内多个取值时用 `IN`（等价于 OR）。

    词表由调用方传入（`all_tags` 的结果）：词表很小，而调用方往往已经
    查过一遍（列表页要算计数、悬浮栏要算分组），不必再查一次。

    返回 `None` 表示没有筛选条件（调用方不必拼 where）。
    """
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return None
    parent_of = {row.id: (row.parent_id or "") for row in tag_rows}
    children = _children_of(tag_rows)
    groups = group_tag_ids_by_dimension(wanted, parent_of)
    if not groups:
        return None
    clauses: list[ColumnElement[bool]] = []
    for group in groups:
        # 组内每个选中的标签展开成「自身 + 全部下级」（选一级 = 它的全部取值）
        expanded: set[str] = set()
        for tag_id in group:
            expanded.update(_descendants(tag_id, children))
        clauses.append(
            exists(
                select(1)
                .select_from(LibraryAssetTag)
                .where(
                    LibraryAssetTag.asset_id == LibraryAsset.id,
                    LibraryAssetTag.tag_id.in_(list(expanded)),
                )
                # 只关联 `LibraryAsset`，**不**关联 `LibraryAssetTag`：
                # 调用方的外层查询可能也带 `LibraryAssetTag`（标签计数那张表
                # 就是「绑定行 JOIN 资产」），自动关联会把外层的同名列拉进子句，
                # 于是子句查的是「这行绑定行的 tag_id 在集合里」而不是
                # 「外层那批资产里有没有挂这个标签」—— 条件恒假，计数全 0。
                .correlate_except(LibraryAssetTag)
            )
        )
    # 维度之间取交集：AND
    return and_(*clauses)


async def assignable_tag_ids(
    session: AsyncSession, tag_ids: Iterable[str]
) -> list[str]:
    """从这批 id 里挑出**可打标**的词：只排除「有子级的一级分组」。

    与下面 `_ensure_tags_exist(reject_groups=True)` 是同一条判据，抽出来给
    「生成产物继承参考素材标签」复用。继承来的词若含维度（产品 / 画面类型），
    它会显示成一个标签却筛不出任何东西 —— 筛选按二级取值匹配，维度只用来分组。
    孤立的一级（删维度后被提升上来的取值）仍可打，与原守卫保持一致。
    """
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return []
    rows = (
        await session.execute(
            select(LibraryTag.id, LibraryTag.parent_id).where(
                LibraryTag.id.in_(wanted)
            )
        )
    ).all()
    has_children = set(
        (
            await session.scalars(
                select(LibraryTag.parent_id)
                .where(LibraryTag.parent_id.in_(wanted))
                .distinct()
            )
        ).all()
    )
    return [
        tag_id for tag_id, parent_id in rows if parent_id or tag_id not in has_children
    ]


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
        # **不可打标 = 有子标签的一级**（它是维度，是分组）。
        #
        # 为什么不是「所有一级」：删掉一个维度时，它的取值会被提升为一级
        # （见 `delete`），若一级一律不可打标，这些取值就成了「看得见却打不上」
        # 的死词 —— 用户删掉「产品」维度后，原本的产品名再也标不上。
        # 同样，存量里「音乐」「AI生成」这些孤立的一级本来就是能直接打的词。
        #
        # 两级模型下这已经覆盖了「维度」：正常结构里一级都带子标签（= 维度），
        # 取值都在二级。
        assignable = set(await assignable_tag_ids(session, wanted))
        blocked = [tag_id for tag_id in wanted if tag_id not in assignable]
        if blocked:
            raise BadRequest("一级标签是分组（维度），请选择它下面的二级标签")


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

    `reject_groups=False` 只给系统内部合并用（生成入库），见
    `ingest` 里的注释；界面上的打标一律走默认值。
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
        purpose: str = "",
        tag_ids: Sequence[str] = (),
    ) -> list[LibraryTagStatOut]:
        """标签栏的数据：全部标签 + 当前入口下各标签的素材数。

        两个口径刻意与 `LibraryService.list_assets` 一致，否则「标签旁写 8，
        点进去只有 3 条」会让人以为数据丢了：

        - `state=active` 带上 `tab_clause`（与列表的可见集同口径）；
        - 草稿 / 回收站按权限点收窄到本人或全量，忽略 `library`。
        - **用途与列表同口径**：不传时排除案例（案例池有自己的入口），传 `case` 时只算案例。
          少了这一条，胶囊上的数字会比列表多——案例素材在列表里看不见，
          却把数字撑大了。

        **`kind` 与列表的类别筛选同口径**：悬浮栏按类别看库时，胶囊上的数字
        必须也是「这一类里有几条」。不按类别算的话，用户在「图片」下看到
        胶囊写 9、点进去只有 4 条——数字说的是全库，筛的是本类。

        **`tag_ids` 是候选范围**（模板库用）：只统计**符合这组标签**的素材。
        模板作者圈了「产品 + 三视图」当候选集，模板库要显示的是
        「这批素材身上实际有的二级标签」——按全库算会列出一堆这批素材
        一个都没挂的标签，用户点了只得到空列表。

        范围的筛选口径与列表完全一致（`tag_filter_clause`：组内 OR、组间 AND），
        否则「标签栏说这条有 3 条，点进去只有 1 条」。

        **不受当前标签筛选影响**：计数是「这个入口里有多少素材属于该标签」，
        不是「再叠上已选标签后还剩多少」；否则每点一次筛选，其余标签的数字
        都会跳一遍，反而没法读。关键词筛选同理不计入。

        排序按 `created_at` 升序（先加的在前），`id` 收尾保证同秒创建时稳定。
        """
        groups = await access.all_groups(self.session, actor)
        rows = await all_tags(self.session)
        _parent, depth, path = tag_tree_index(rows)
        children = _children_of(rows)

        per_tag = await self._asset_ids_by_tag(
            actor, groups, state, library, kind, purpose, tag_ids
        )
        # 一个标签的数字 = 自身 + 全部下级，按资产 id 去重。这与「点一级展开到
        # 全部下级」的筛选口径完全一致；`count(distinct)` 做不到跨标签的并集去重，
        # 所以这里把绑定行取回来在内存里归并（当前入口的可见集本来就不大）。
        counts: dict[str, int] = {}
        for row in rows:
            asset_ids: set[str] = set()
            for tag_id in _descendants(row.id, children):
                asset_ids.update(per_tag.get(tag_id, ()))
            counts[row.id] = len(asset_ids)

        return [
            LibraryTagStatOut(
                id=row.id,
                name=row.name,
                parent_id=row.parent_id or None,
                # 父的整条链（"一级 / 二级"）：拼上自己的名字就是卡片上那行
                parent_name=path.get(row.parent_id) if row.parent_id else None,
                depth=depth.get(row.id, 1),
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
        purpose: str = "",
        tag_ids: Sequence[str] = (),
    ) -> dict[str, set[str]]:
        """当前入口下「每个标签 → 可见资产 id 集合」，一次查询取回。

        可见条件与 `LibraryService.list_assets` 一字不差；比旧的 `group by count`
        多取一列 asset_id，是为了给一级标签做「自身 ∪ 子级」的资产级去重。

        `kind` 是类别筛选（image / video / audio / text），与列表的类别口径
        一致；留空表示不按类别收窄。`purpose` 是用途，口径同理。

        `tag_ids` 是候选范围：只统计符合这组标签的素材（口径与列表的
        `tag_filter_clause` 一致 —— 组内 OR、组间 AND）。留空 = 不按标签收窄。
        """
        query = (
            select(LibraryAssetTag.tag_id, LibraryAssetTag.asset_id)
            .join(LibraryAsset, LibraryAsset.id == LibraryAssetTag.asset_id)
            .where(LibraryAsset.state == state)
        )
        if kind:
            query = query.where(LibraryAsset.kind == kind)
        # 用途与列表同口径：普通入口排除案例，案例池只算案例。
        # 不对齐就会出现「标签旁写 8，点进去只有 3 条」——
        # 案例素材在列表里看不见，却把胶囊的数字撑大了。
        if purpose:
            query = query.where(LibraryAsset.purpose == purpose)
        elif state == "active":
            query = query.where(LibraryAsset.purpose != access.PURPOSE_CASE)
        if state == "active":
            query = query.where(
                LibraryAsset.deleted_at.is_(None),
                access.tab_clause(actor, groups, library),
            )
        elif state == "draft":
            if not actor.has(access.PERM_DRAFT_REVIEW):
                query = query.where(LibraryAsset.owner_id == actor.id)
        elif not actor.has(access.PERM_DELETE_ANY):
            query = query.where(LibraryAsset.owner_id == actor.id)

        # 候选范围：与列表同一个 `tag_filter_clause`（组内 OR、组间 AND）。
        # 这里筛的是**素材**，上面那段查的是「素材 × 标签」的绑定行 ——
        # 范围条件落在素材上，绑定行上的标签才是「这批素材身上的标签」。
        if tag_ids:
            clause = tag_filter_clause(await all_tags(self.session), tag_ids)
            if clause is not None:
                query = query.where(clause)

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
        """同名幂等的口径收窄到「同一父下」：两个维度各自有「小红书」是合法的。

        两级模型：`parentId` 只能指向**一级**（维度）。指向二级会被拒 ——
        那是「三级」，维度模型里不存在。
        """
        name = payload.name.strip()
        parent_id = payload.parent_id or ""
        parent_name: str | None = None
        depth: dict[str, int] = {}
        if parent_id:
            parent = await self.session.get(LibraryTag, parent_id)
            if parent is None:
                raise BadRequest("父标签不存在")
            _parent, depth, path = tag_tree_index(await all_tags(self.session))
            if depth.get(parent_id, 1) >= MAX_TAG_DEPTH:
                raise BadRequest(
                    f"标签最多 {MAX_TAG_DEPTH} 级：一级是维度（分组），"
                    "取值只能挂在维度下面"
                )
            parent_name = path.get(parent_id) or parent.name
        existing = await self.session.scalar(
            select(LibraryTag).where(
                LibraryTag.name == name, LibraryTag.parent_id == parent_id
            )
        )
        if existing is not None:
            return to_tag_out(existing, parent_name, depth.get(existing.id, 1))
        # 新行还没进树,查不到它的层数 —— 自己算:父的层数 + 1(顶级就是 1)
        new_depth = depth.get(parent_id, 1) + 1 if parent_id else 1
        row = LibraryTag(name=name, parent_id=parent_id, created_by=actor.id)
        self.session.add(row)
        await self.session.commit()
        return to_tag_out(row, parent_name, new_depth)

    async def rename(self, tag_id: str, payload: TagIn) -> LibraryTagOut:
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        _parent, depth, path = tag_tree_index(await all_tags(self.session))
        # `parentName` 是**父的整条链**（「产品 / 3030」），与列表、创建一致：
        # 直系父名会让同一个标签在两个接口里显示成两样。
        parent_name = path.get(row.parent_id) if row.parent_id else None
        name = payload.name.strip()
        if name == row.name:
            return to_tag_out(row, parent_name, depth.get(row.id, 1))
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
        return to_tag_out(row, parent_name, depth.get(row.id, 1))

    async def move(self, tag_id: str, payload: TagParentIn) -> LibraryTagOut:
        """改层级：`parentId=null` 提升为一级（变成维度），指向一级则降为二级（取值）。

        四道守卫：不能挂到自己下面；不能挂到自己的下级下面（会成环）；
        挂过去之后**整棵子树**都不能超过两层；目标父下不能有同名。
        前两条防环，后一条防「挂上去之后层级超限」——子树的高度算得出来，
        不必等下一次报错才发现。
        """
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        parent_id = payload.parent_id or ""
        if parent_id == row.id:
            raise BadRequest("标签不能挂到自己下面")
        parent_name: str | None = None
        depth: dict[str, int] = {}
        if parent_id:
            parent = await self.session.get(LibraryTag, parent_id)
            if parent is None:
                raise BadRequest("父标签不存在")
            rows = await all_tags(self.session)
            _parent, depth, path = tag_tree_index(rows)
            children = _children_of(rows)
            if parent_id in _descendants(row.id, children):
                raise BadRequest("标签不能挂到自己的下级下面")
            # 整棵子树挂过去后最深的那一层的层数：父的层数 + 自己的子树高度
            own_depth = depth.get(row.id, 1)
            subtree_max = max(
                depth.get(item, own_depth) for item in _descendants(row.id, children)
            )
            # 子树有几层：自己这层算第一层
            height = subtree_max - own_depth + 1
            # 挂过去之后：自己的层数 = 父的层数 + 1，整棵子树跟着平移
            if depth.get(parent_id, 1) + height > MAX_TAG_DEPTH:
                raise BadRequest(
                    f"标签最多 {MAX_TAG_DEPTH} 级：一级是维度，取值只能挂在维度下面"
                )
            parent_name = path.get(parent_id) or parent.name
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
        # 移动之后层数变了：出参要按**新**位置算，不能用移动前那张快照
        new_depth = depth.get(parent_id, 1) + 1 if parent_id else 1
        return to_tag_out(row, parent_name, new_depth)

    async def delete(self, tag_id: str) -> None:
        """删标签同时清绑定；**旗下子级提升一级**，不级联删。

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
            # 子级提升到「自己原来的父」那一层：删一级（维度）→ 二级升一级。
            # 提升后它们成了孤立的一级 —— 打标守卫认的是「有子标签的一级」
            # 才不可打标，所以提升上来的取值仍然能正常打在素材上。
            # 提升后撞名要按**那一层**判，不是按一级判。
            target_parent = row.parent_id or ""
            sibling_names = set(
                (
                    await self.session.scalars(
                        select(LibraryTag.name).where(
                            LibraryTag.parent_id == target_parent,
                            LibraryTag.id != row.id,
                        )
                    )
                ).all()
            )
            clash = [child.name for child in children if child.name in sibling_names]
            if clash:
                # 提升后 `(name, parent_id)` 会撞唯一索引；与其报 500，
                # 不如把「先给子标签改个名」这件事说清楚。
                raise Conflict(f"子标签与同层已有标签同名，请先改名：{', '.join(clash[:5])}")
            await self.session.execute(
                update(LibraryTag)
                .where(LibraryTag.parent_id == row.id)
                .values(parent_id=target_parent)
            )
        await self.session.execute(
            delete(LibraryAssetTag).where(LibraryAssetTag.tag_id == tag_id)
        )
        await self.session.delete(row)
        await self.session.commit()
