"""归档素材仓储：`assets` 表是索引，`catalog/assets/*.json` 是只读镜像。

## 为什么不再 glob JSON

以前列表是「扫 `catalog/assets/*.json` 再按 owner 过滤」：共享盘上文件一多，
每次翻页都要把整个目录读一遍；而且 JSON 与数据库各自记一份 `owner_id`，
权限判断就变成了「信哪个文件」的问题。现在查询走 `assets` 表（`source='archive'`），
分页与过滤交给数据库。

## JSON 为什么还写

替换模板（`modules/template/replacement_*`）在 worker 里同步读
`catalog/assets/{id}.json`，是「零改动」的前提。所以新入库的归档素材
**同时写一份 JSON**，内容由 DB 行的同一套映射派生，不额外维护第二份真相。

写失败即整次入库失败（回滚行、删掉刚复制的对象）：留下「有行没 JSON」的记录，
替换模板会在 `prepare` 阶段报「素材不存在」，而用户看到的却是列表里明明有。

## 目录归属

`storage_key` 是**归档根**下的相对 key（如 `library/source/2026/11/xxx.mp4`），
与上传素材（相对对象存储根）不是同一个命名空间。区别靠 `source` 列表达：
`archive` 走 `ArchiveStorage`，其余走 `ObjectStorage`。不要拿这个 key 去问
`get_storage()`，那是另一棵树。
"""

from __future__ import annotations

from typing import Literal, cast

from sqlalchemy import func, select, union_all
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import NotFound
from core.media import MediaCategory, StoredMedia
from db.repository import BaseRepository
from modules.asset.library_models import Blob
from modules.asset.lifecycle import LIVE_STAGE
from modules.asset.models import Asset
from modules.asset.schema import AssetSource
from modules.asset.tag_models import AssetTag

CATEGORIES = ("source", "character", "product", "scene", "audio")

# 归档素材只允许这五种分类；库里为 NULL 的行不是归档来源，读出来也不该假装是
_DEFAULT_CATEGORY: MediaCategory = "source"

AssetKind = Literal["image", "video", "audio"]


class ArchiveAssetRepository:
    """按 `source='archive'` 限定作用域的素材仓储（异步）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, Asset)

    async def get(self, asset_id: str) -> Asset:
        """按 id 取归档行；格式不对、不存在、不是归档来源，一律「素材不存在」。"""
        if len(asset_id) != 32 or any(c not in "0123456789abcdef" for c in asset_id):
            raise NotFound("素材不存在")
        row = await self.repo.get(asset_id)
        if row is None or row.source != AssetSource.ARCHIVE:
            raise NotFound("素材不存在")
        return row

    async def list_owned(self, owner: str, category: str = "all") -> list[Asset]:
        """某人的全部归档行（不分页）。列表接口走 `list_visible`，这里留给脚本与统计。

        方法名不叫 `list`：类体内 `list` 会遮蔽内置类型，`tuple[list[Asset], ...]`
        这类注解会被 mypy 解析成「方法不是类型」。
        """
        stmt = (
            select(Asset)
            .where(
                Asset.owner_id == owner,
                Asset.source == AssetSource.ARCHIVE,
            )
            .order_by(Asset.created_at.desc(), Asset.id.desc())
        )
        if category != "all":
            stmt = stmt.where(Asset.category == category)
        return list((await self.session.scalars(stmt)).all())

    async def distinct_used(
        self,
        *,
        visibility: str,
        owner_id: str | None = None,
        team_id: str | None = None,
        deleted: bool = False,
    ) -> int:
        """某个计费主体的**账面**占用（字节）。

        口径是「按 `sha256` 去重后的未删除素材」：同一份文件复制到 5 个分类只算
        一次。这是跨库复用能成立的前提——按条数或按行求和，复制就等于计费翻倍，
        用户会立刻学会「不复制、直接改别人的」，而存储成本一点没涨。

        摘要为空的行不参与合并（`sha256 IS NULL` 多条会是不同的文件），
        所以分两段 `UNION ALL`：有摘要的按摘要取一次大小，没摘要的逐条算。
        写成一个 `GROUP BY sha256` 会把所有 NULL 归成一组，只算一份——
        那会低估占用，而且是被静默低估的。
        """
        conditions = [Asset.source == AssetSource.ARCHIVE, Asset.visibility == visibility]
        # 只有已采纳的才占额度。候选与废片也占真实字节，但它们**不该记在谁头上**：
        # 计入的话，一次 8 连拍就会让用户以为额度被吃了 8 倍，而其中 7 张会被弃用。
        conditions.append(Asset.stage == LIVE_STAGE)
        if owner_id is not None:
            conditions.append(Asset.owner_id == owner_id)
        if team_id is not None:
            conditions.append(Asset.team_id == team_id)
        conditions.append(
            Asset.deleted_at.is_not(None) if deleted else Asset.deleted_at.is_(None)
        )

        with_digest = (
            select(func.max(Asset.size_bytes).label("size"))
            .where(*conditions, Asset.sha256.is_not(None))
            .group_by(Asset.sha256)
        )
        without_digest = select(Asset.size_bytes.label("size")).where(
            *conditions, Asset.sha256.is_(None)
        )
        merged = union_all(with_digest, without_digest).subquery()
        return int(
            await self.session.scalar(select(func.coalesce(func.sum(merged.c.size), 0))) or 0
        )

    async def used_by_owner(self, owner: str) -> int:
        """个人额度占用。名字保留：`_ingest` 与 doctor 都按它取数。"""
        return await self.distinct_used(visibility="personal", owner_id=owner)

    async def physical_used(self) -> int:
        """物理占用：`blobs` 表里所有字节之和。

        与账面的差别是**回收站**：里面的素材账面上已释放，字节却还在
        （随时可能被恢复，不能在恢复时因为「额度已满」失败）。
        两个数字都报，用户才能理解「删了一堆空间没变」不是 bug。

        **不能加 `ref_count > 0` 条件**：软删把引用减到 0 时字节并没有消失，
        加了条件会让物理占用跟着账面一起掉——那正好把「账面释放、物理不释放」
        这条差异抹平，而这条差异正是要显式呈现的东西。真正的释放只发生在
        清理任务删掉 blob 行之后。
        """
        return int(
            await self.session.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))) or 0
        )

    async def draft_used(self) -> int:
        """草稿桶占用：`tier='draft'` 且仍被引用的字节。

        单独报的原因：它占真实空间（不该藏），但混进总量会让用户误以为自己超标
        （不该并入）。M3 之前没有草稿桶，这个数恒为 0——接口先留位置，
        免得前端在 M3 之后再改一次字段。
        """
        return int(
            await self.session.scalar(
                select(func.coalesce(func.sum(Blob.size_bytes), 0)).where(Blob.tier == "draft")
            )
            or 0
        )

    async def lock_owner_for_quota(self, owner: str) -> None:
        """配额判定前把同一用户的登记串行化。

        「读 sum → 判上限 → 写行」三步必须在一个临界区里，否则两个并发上传会各自
        看到「还剩 100MB」然后都写进去。PostgreSQL 下用 `FOR UPDATE` 锁住该 owner
        已有的一行：同一用户的并发登记会排队，不同用户互不影响。

        已知边界（doctor 会报）：SQLite 没有行锁，且用户**首次**登记时无行可锁，
        这两种情况仍可能超限。彻底原子化要引入 `usage` 表的条件 UPDATE，那会多一处
        双写；当前选择是「绝大多数并发安全 + 边界显式化」，而不是假装已经解决。
        """
        if settings.is_sqlite:
            return
        await self.session.scalar(
            select(Asset.id)
            .where(
                Asset.owner_id == owner,
                Asset.source == AssetSource.ARCHIVE,
            )
            .with_for_update()
            .limit(1)
        )

    async def list_visible(
        self,
        owner: str,
        category: str,
        scope: str,
        keyword: str,
        state: str,
        page: int,
        page_size: int,
        tag_ids: tuple[str, ...] = (),
        sort: str = "created_desc",
        team_id: str = "",
        category_id: str = "",
        media_type: str = "",
    ) -> tuple[list[Asset], int, int]:
        """按 scope 取一页，并给出「全部命中行」的条数与占用。

        `total` / `usedBytes` 是**过滤后的全量**，不是当前页：前端用它们显示
        「共 N 个 / 占 X GB」，拿当前页求和会随着翻页跳数字。

        过滤全部下推到 SQL：这是索引搬进 DB 的意义，别在 Python 里对全表做
        `keyword in name` —— 那等于把 glob 时代的问题原样搬过来。
        """
        conditions = [Asset.source == AssetSource.ARCHIVE, Asset.stage == LIVE_STAGE]
        if media_type:
            # 二级「媒体类型」分面不是分类节点（C1），它只是同一个分类下的
            # 一次过滤。用独立列 `media_type` 而不是 `type`：后者是历史列，
            # 由客户端声明，而分面必须按系统判定值分组，否则改过名的文件
            # 会出现在错误的组里。
            conditions.append(Asset.media_type == media_type)
        if category_id:
            # 新分类树走 id：分类名可以改，id 不能——按名字过滤的话，
            # 用户把「产品图」改成「产品白底」之后，书签里的筛选会静默变成空结果。
            conditions.append(Asset.category_id == category_id)
        elif category != "all":
            conditions.append(Asset.category == category)
        if scope == "trash":
            conditions += [
                Asset.owner_id == owner,
                Asset.deleted_at.is_not(None),
            ]
        elif scope == "team":
            # 团队库：按 `visibility + team_id` 取，**不按 owner**。
            # 写成 `owner == me` 会让团队库退化成个人库，而界面上看不出区别——
            # 只是"同事传的东西我看不到"，通常被理解成权限问题而不是查询写错。
            conditions += [
                Asset.deleted_at.is_(None),
                Asset.visibility == "team",
                Asset.team_id == (team_id or "\x00"),
            ]
        elif scope == "public":
            # 公共原片池：别人分享的、未删除的原片。只认 `source` 分类，
            # 人物/产品/场景走封装（pack），不在这里按图分享。
            conditions += [
                Asset.deleted_at.is_(None),
                Asset.shared.is_(True),
                Asset.category == "source",
            ]
        else:  # mine
            # 个人库 = `visibility=personal` **且**我拥有。
            #
            # 只按 owner 不加可见域，会让「归档进团队库的素材」同时出现在
            # 我的素材里——而它的 `category_id` 在**团队**那棵树上，个人树的
            # 角标统计不到它。症状是「个人库共 9 项，分类角标加起来 5」，
            # 这种差 4 条的对不上账只能靠人工发现。
            #
            # 一条素材只有一个"家"（可见域是单一值 §7.1）：团队素材的家在团队库，
            # 个人库不该是它的第二个入口。要拿回自己库里走「取用」复制。
            conditions += [
                Asset.owner_id == owner,
                Asset.visibility == "personal",
                Asset.deleted_at.is_(None),
            ]
        if keyword.strip():
            conditions.append(Asset.name.ilike(f"%{keyword.strip()}%"))
        if state != "all":
            conditions.append(Asset.shared.is_(state == "shared"))
        if tag_ids:
            # **AND 语义**：多个标签是「同时具备」。写成 `IN` 再 `HAVING COUNT(*) = N`
            # 而不是链 N 个 `EXISTS`：前者一次索引扫描，后者 N 次。（`asset_id, tag_id`
            # 是复合主键，同一条素材不会重复命中同一个词，所以不必 `COUNT(DISTINCT)`。）
            matched = (
                select(AssetTag.asset_id)
                .where(AssetTag.tag_id.in_(tag_ids))
                .group_by(AssetTag.asset_id)
                .having(func.count(AssetTag.tag_id) == len(tag_ids))
            )
            conditions.append(Asset.id.in_(matched))

        total = await self.session.scalar(
            select(func.count()).select_from(select(Asset.id).where(*conditions).subquery())
        )
        used = await self.session.scalar(
            select(func.coalesce(func.sum(Asset.size_bytes), 0)).where(*conditions)
        )
        # 排序必须下推到 SQL：只在当前页排会让「按名称」在翻页时变成四段互不
        # 相接的局部有序，而用户以为是全量有序。
        # 都用 `id` 作第二排序键：`created_at` 在批量导入时可能整批相同，
        # 没有决胜键时翻页会出现重复/漏行。
        #
        # 分三支写而不是查表后 `order_by(*tuple)`：三种排序的列类型不同
        # （`str` 与 `datetime`），装进一个元组会让 mypy 退化成 `object`，
        # 而 `order_by` 不接受 `object`——那正是"类型系统在提醒这里不该合并"。
        statement = select(Asset).where(*conditions)
        if sort == "created_asc":
            statement = statement.order_by(Asset.created_at.asc(), Asset.id.asc())
        elif sort == "name_asc":
            statement = statement.order_by(Asset.name.asc(), Asset.id.asc())
        else:
            statement = statement.order_by(Asset.created_at.desc(), Asset.id.desc())
        rows = list(
            (
                await self.session.scalars(
                    statement.offset((page - 1) * page_size).limit(page_size)
                )
            ).all()
        )
        return rows, int(total or 0), int(used or 0)

    async def save(self, row: Asset) -> Asset:
        """把内存里的改动落库；提交由 service 控制（一次操作一个事务）。"""
        await self.session.flush()
        return row

    async def add(self, row: Asset) -> Asset:
        self.session.add(row)
        await self.session.flush()
        return row

    @staticmethod
    def to_media(row: Asset) -> StoredMedia:
        """DB 行 → 归档出参。

        路径与 URL 都按「归档根」拼，`path` 是前端下载时拼 Z 盘路径用的相对 key。

        **字节字段优先取 `blob`**：同一份内容只有一条 blob，换桶、迁移、
        去重都只改一处。`blob` 为空（回填还没跑到的存量行、演示用的远端占位图）
        时回落旧列——那条回落路径在 M1-G 删列后消失，届时可以读成一个断言。

        这里**不能用 `row.blob` 直接取**：新构造的对象与 `commit` 之后的对象
        都处于「未加载」状态，异步会话里访问它会触发隐式懒加载，
        报 `MissingGreenlet` 并带一长串无关调用栈。用 `unloaded` 判断，
        没加载过就当没有，反正旧列还在。
        """
        blob = _loaded_blob(row)
        storage_key = blob.storage_key if blob is not None else row.storage_key
        preview_key = (blob.preview_key or None) if blob is not None else (row.cover_key or None)
        base = f"{settings.api_prefix}/assets/{row.id}"
        return StoredMedia(
            id=row.id,
            name=row.name,
            category=cast(MediaCategory, row.category or _DEFAULT_CATEGORY),
            type=cast(AssetKind, row.type),
            mime_type=(blob.mime_type if blob is not None else row.mime_type),
            size_bytes=(blob.size_bytes if blob is not None else row.size_bytes),
            path=storage_key,
            sha256=((blob.sha256 or "") if blob is not None else (row.sha256 or "")),
            owner_id=row.owner_id,
            created_at=row.created_at,
            width=row.width,
            height=row.height,
            duration=row.duration,
            has_audio=row.has_audio,
            url=f"{base}/file",
            preview_url=f"{base}/thumbnail" if preview_key else None,
            preview_path=preview_key,
            # 共享 / 回收站状态跟着行走：JSON 镜像由这里派生，
            # `MediaCatalog` 读镜像判断「已删除就不许模板引用」，两处必须同源。
            # 兜 None：ORM 的 `default=` 要到 flush 才落值，而镜像是在 flush 之前写的。
            shared=bool(row.shared),
            shared_at=row.shared_at,
            deleted_at=row.deleted_at,
            state_version=row.state_version or 0,
        )


def _loaded_blob(row: Asset) -> Blob | None:
    """只在已经加载时返回 `row.blob`，**绝不触发懒加载**。"""
    if "blob" in sa_inspect(row).unloaded:
        return None
    return row.blob
