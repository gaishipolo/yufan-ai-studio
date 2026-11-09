"""库边界与分类树的服务层：团队查询、分类树幂等初始化。

## 为什么分类树需要「幂等初始化」而不是一次性建好

三棵树的创建时机不一样，而它们都必须存在：

| 库 | 谁建 | 时机 |
| --- | --- | --- |
| 品牌公共库 | 迁移 | `b7d2f0c4e815` 升级时 |
| 团队库 | `bootstrap` | 每次启动（团队可能被新增） |
| 个人库 | 首次上传 / 首次打开素材页 | 懒建——注册时建树等于给只用任务中心的人白写 6 行 |

所以入口必须能反复调用且不产生第二棵收件箱。唯一索引 `uq_categories_inbox`
是最后一道闸：并发两个请求同时建树时，一个会拿到 `IntegrityError`，
这里捕获后重查——**不是**「先查后插」就够，那个检查在并发下必然穿透。

## 为什么「用途」这五个名字是常量而不是配置

它们是分类树的初始内容，不是规则。用户可以改名、排序、加自己的分类；
但**新建一棵树时该长什么样**是一个产品决定，写代码比写数据库更可审。
改这五个词等于改所有新用户的第一印象，应当是代码评审而不是后台点一下。
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest, Forbidden
from core.logging import get_logger
from modules.asset.library_models import Category, Team, TeamMember

logger = get_logger(__name__)

__all__ = [
    "INBOX_NAME",
    "PURPOSES",
    "LibraryService",
    "ensure_tree",
    "lazy_ensure_personal_tree",
]

# 初始用途分类。顺序就是分类条的默认顺序（sort_order 按 10 递增，留出插空位）。
PURPOSES: Final[tuple[str, ...]] = ("参考素材", "产品图", "人物形象", "场景背景", "合成成片")

# 系统收件箱的名字。带「未分类」三个字是刻意的：它要能被用户一眼认成
# 「东西在这里但没归类」，而不是一个叫「收件箱」的普通分类。
INBOX_NAME: Final[str] = "未分类收件箱"


async def ensure_tree(
    session: AsyncSession, scope: str, owner_key: str, *, commit: bool = True
) -> dict[str, str]:
    """确保某棵树存在，返回 `{分类名: id}`。

    `commit=False` 供调用方把自己的写入与建树放进同一个事务（上传路径要这样：
    树建好了但素材没写成，会留下一个空分类树，没有害处但没必要）。
    """
    existing = (
        await session.scalars(
            select(Category).where(Category.scope == scope, Category.owner_key == owner_key)
        )
    ).all()
    by_name = {row.name: row.id for row in existing}
    if any(row.is_inbox for row in existing):
        return by_name

    rows: list[Category] = []
    if INBOX_NAME not in by_name:
        rows.append(
            Category(
                scope=scope,
                owner_key=owner_key,
                name=INBOX_NAME,
                kind="system",
                is_inbox=True,
                sort_order=0,
            )
        )
    for index, name in enumerate(PURPOSES, start=1):
        if name in by_name:
            continue
        rows.append(
            Category(
                scope=scope,
                owner_key=owner_key,
                name=name,
                kind="system",
                is_inbox=False,
                sort_order=index * 10,
            )
        )
    if rows:
        try:
            # savepoint 而不是整事务回滚：调用方（上传、seed）同一个事务里
            # 可能已经写了别的行，把它们的成果一起回滚掉是灾难性的
            # ——症状是「上传成功但对象没建成」或「团队建了成员没建」。
            async with session.begin_nested():
                session.add_all(rows)
                await session.flush()
        except IntegrityError:
            # 并发建树：另一个请求先插进去了。重查，返回对方建好的那棵
            # ——用户拿到的是「树就在那儿」，不是一条主键冲突。
            existing = (
                await session.scalars(
                    select(Category).where(Category.scope == scope, Category.owner_key == owner_key)
                )
            ).all()
            by_name = {row.name: row.id for row in existing}
            return by_name
    if commit:
        await session.commit()
    by_name.update({row.name: row.id for row in rows})
    return by_name


async def lazy_ensure_personal_tree(session: AsyncSession, user_id: str) -> str:
    """个人库的分类树按需建，返回收件箱 id。"""
    tree = await ensure_tree(session, "personal", user_id, commit=False)
    return tree.get(INBOX_NAME, "")


class LibraryService:
    """团队与分类树的读模型。写操作（建团队、改成员）留给 M5 的团队管理。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def teams_for(
        self, user_id: str, *, visible_to_all: bool
    ) -> list[tuple[Team, str, int]]:
        """返回 `[(团队, 我在团队里的角色, 成员数)]`。

        `visible_to_all` 由权限点决定，不由身份判断：`asset:team:manage` 的持有者
        要能看到全部团队（否则他没法管理自己不在的团队），普通成员只看自己的。

        **跨团队不可见**（A1 的结论）在这里就生效，不等 M5：
        M5 补的是「往上递」那两条流程，不是「能不能看见」。
        """
        counts = dict(
            (
                await self.session.execute(
                    select(TeamMember.team_id, func.count())
                    .select_from(TeamMember)
                    .group_by(TeamMember.team_id)
                )
            ).all()
        )
        if visible_to_all:
            teams = list(
                (
                    await self.session.scalars(
                        select(Team).where(Team.status == "active").order_by(Team.created_at)
                    )
                ).all()
            )
            roles = dict(
                (
                    await self.session.execute(
                        select(TeamMember.team_id, TeamMember.role).where(
                            TeamMember.user_id == user_id
                        )
                    )
                ).all()
            )
            return [(team, roles.get(team.id, ""), int(counts.get(team.id, 0))) for team in teams]

        rows = list(
            (
                await self.session.execute(
                    select(Team, TeamMember.role)
                    .join(TeamMember, TeamMember.team_id == Team.id)
                    .where(TeamMember.user_id == user_id, Team.status == "active")
                    .order_by(Team.created_at)
                )
            ).all()
        )
        return [(team, role, int(counts.get(team.id, 0))) for team, role in rows]

    async def library_key(
        self, owner_id: str, library: str, *, visible_to_all: bool
    ) -> tuple[str, str]:
        """把顶栏的 `library` 参数翻译成 `(scope, owner_key)`，顺带做可见性判定。

        `library` 的取值是 `personal` / `brand` / `team:<id>`，与前端
        `model/library.ts` 的 `LibraryId` 同源——**这个翻译只写一处**。
        散落到各接口里之后，「个人库」在列表里是 owner、在分类树里是 owner_key、
        在配额里是 visibility，三处一旦不同步，就会出现「列表有 12 条、
        分类角标说 0 条」这种对不上的账。
        """
        if library in ("", "personal"):
            return "personal", owner_id
        if library == "brand":
            return "brand", ""
        if not library.startswith("team:"):
            raise BadRequest("库参数无效")
        team_id = library[len("team:") :]
        visible = await self.teams_for(owner_id, visible_to_all=visible_to_all)
        if team_id not in {team.id for team, _, _ in visible}:
            # 不是成员就 403，不是「200 + 空列表」：空列表会被读成
            # 「这个团队还没传东西」，而用户会去问管理员要权限
            raise Forbidden("无权访问该团队库")
        return "team", team_id

    async def categories(
        self, owner_id: str, library: str, *, visible_to_all: bool
    ) -> list[tuple[Category, int]]:
        """当前库的分类树 + 每个分类下的素材数，按 `sort_order`。

        计数是**未删除、当前库可见**的素材数，口径必须与列表查询一致，
        否则分类条写「产品图 149」点进去只有 12 条——用户会认为筛选坏了。
        所以这里的条件表达式与 `AssetRepository.list_visible` 的分支一一对应，
        改动任意一边都要同时改另一边（`test_asset_categories` 里有一条对账用例）。
        """
        from modules.asset.lifecycle import LIVE_STAGE
        from modules.asset.models import Asset
        from modules.asset.schema import AssetSource

        scope, owner_key = await self.library_key(owner_id, library, visible_to_all=visible_to_all)
        if scope == "personal":
            # 个人树懒建：只用任务中心的人不该被写 6 行分类
            await ensure_tree(self.session, "personal", owner_key, commit=False)
        rows = list(
            (
                await self.session.scalars(
                    select(Category)
                    .where(Category.scope == scope, Category.owner_key == owner_key)
                    .where(Category.archived_at.is_(None))
                    .order_by(Category.sort_order, Category.name)
                )
            ).all()
        )

        conditions = [
            Asset.source == AssetSource.ARCHIVE,
            Asset.deleted_at.is_(None),
            # 与列表的 `stage` 过滤对齐：角标数的是**已采纳**的素材。
            # 少了它，候选一生成就出现在分类角标里，而列表里看不到它们——
            # 差额没有任何去处，正是"共 12 条、分类加起来 20"的来源。
            Asset.stage == LIVE_STAGE,
        ]
        if scope == "personal":
            # 与列表的 `mine` 分支逐条对齐：**含 `visibility=personal`**。
            # 少了它，归档进团队库的素材会被算进个人树（它们的位置在团队树上），
            # 于是角标比列表多——和"角标比列表少"是同一个 bug 的两面。
            conditions += [Asset.owner_id == owner_key, Asset.visibility == "personal"]
        elif scope == "brand":
            # 与 `list_visible` 的 `public` 分支**逐条对齐**（含那条 `category`
            # 限制）。公共池目前只认原片：人物/产品/场景走封装，不按图分享。
            # 这里"顺手放开"会让角标比列表多，而用户只会认为筛选坏了。
            conditions += [Asset.shared.is_(True), Asset.category == "source"]
        else:
            conditions += [Asset.visibility == "team", Asset.team_id == owner_key]
        counts = dict(
            (
                await self.session.execute(
                    select(Asset.category_id, func.count())
                    .where(*conditions)
                    .group_by(Asset.category_id)
                )
            ).all()
        )
        # 认不出位置的都算进收件箱：`category_id` 为空（旧写入路径、直接建表的
        # 脚本、迁移没跑回填的存量行）或指向已归档/别的库的分类。
        #
        # 这一步不是兜底而是**必需**：不收进收件箱的话，角标之和会小于列表总数，
        # 差额没有任何去处——用户看到「共 213、分类加起来 205」，第一反应是
        # "是不是有东西丢了"，而实际上只是那 8 条没归类。
        known = {row.id for row in rows}
        unplaced = sum(count for key, count in counts.items() if key not in known)
        counts = {key: count for key, count in counts.items() if key in known}
        inbox = next((row.id for row in rows if row.is_inbox), "")
        if unplaced and inbox:
            counts[inbox] = counts.get(inbox, 0) + unplaced
        return [(row, int(counts.get(row.id, 0))) for row in rows]

    async def ensure_all_team_trees(self) -> int:
        """给公共库与每个 active 团队建分类树。`bootstrap` 调用，幂等。

        公共库也要在这里建：迁移里建过一棵，但 `app seed --reset` 会按 metadata
        清表，清完就只剩空的。把「该有哪些树」收在一个入口，就不会出现
        「演示库有、生产库没有」这种只在一种环境里复现的差异。
        """
        await ensure_tree(self.session, "brand", "", commit=False)
        teams = list(
            (await self.session.scalars(select(Team).where(Team.status == "active"))).all()
        )
        for team in teams:
            await ensure_tree(self.session, "team", team.id, commit=False)
        await self.session.commit()
        return len(teams)


async def bootstrap_library() -> None:
    """模块启动钩子：让团队与它们的分类树对齐。

    只做装配（谁跑都一样、不跑就出错），不改写业务状态——所以放在
    `ModuleSpec.bootstrap` 而不是 `startup`。后者只在 worker 实例执行，
    只读 API 实例不跑，于是「团队没有分类树」这种状态会长期存在。
    """
    from db.session import get_session_factory

    async with get_session_factory()() as session:
        service = LibraryService(session)
        await service.ensure_all_team_trees()
        # 词表也在这里补齐：它和分类树是同一类东西（"每个库启动时该有什么"），
        # 放在 seed 里只会让 `--reset` 之后的环境缺词表，筛选条直接空掉。
        from modules.asset.tag_service import ensure_vocabulary

        added = await ensure_vocabulary(session, commit=True)
        if added:
            logger.info("补齐标签词表：新增 %s 个词", added)
