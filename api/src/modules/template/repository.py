"""模板库的数据访问层：封装与替换运行的 SQL。

分两个仓储而不是一个：封装的读写是「运营配置」节奏（低频、整块），替换运行是
「任务状态」节奏（高频、逐字段更新）。合在一起会让两边共享同一个事务边界，
而它们本来就该各自提交——保存草稿不该顺带把别人的任务状态也锁住。

**没有一处 `select(Model)` 全表读**：列表查询都带 owner 或 status 前缀，
分页走 `limit/offset`，排序键以主键收尾（见 `docs/EXTENDING.md` §1.1）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import Select, and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Conflict, NotFound
from modules.template.models import (
    ReplacementReview,
    ReplacementRun,
    ReplacementRunInput,
    TemplatePack,
    TemplatePackReference,
    TemplatePackVersion,
)


class RunInputRow(NamedTuple):
    """一条「这次实际用到的素材」。

    `asset_id_space` 是**必填**：两套 id 空间形状相同，靠默认值猜空间等于让
    历史任务在常量翻转后被重新解释（`authorization_gap` 会拿错的表去查，
    报「输入已被删除」）。调用方手里本来就知道来源，别在这里省一个字段。
    """

    asset_id: str
    asset_id_space: str
    role: str
    position: int
    usage: str = ""
    storage_key: str = ""


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# 封装
# --------------------------------------------------------------------------- #
class PackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, pack_id: str) -> TemplatePack | None:
        return await self.session.get(TemplatePack, pack_id)

    async def get_or_404(self, pack_id: str, *, owner: str | None = None) -> TemplatePack:
        """`owner` 给了就顺带校验归属：不匹配一律 404，不泄露「存在但不是你的」。"""
        pack = await self.get(pack_id)
        if pack is None or pack.deleted_at is not None:
            raise NotFound("封装不存在")
        if owner is not None and pack.owner_id != owner:
            raise NotFound("封装不存在")
        return pack

    async def list_owned(self, owner: str) -> Sequence[TemplatePack]:
        stmt = (
            select(TemplatePack)
            .where(
                TemplatePack.owner_id == owner,
                TemplatePack.deleted_at.is_(None),
            )
            .order_by(TemplatePack.updated_at.desc(), TemplatePack.id)
        )
        return (await self.session.scalars(stmt)).all()

    async def list_published(self, kind: str | None = None) -> Sequence[TemplatePack]:
        # `select(Model)` 在这一版 SQLAlchemy 的类型里是 `Select[Model]`，
        # 不是 `Select[tuple[Model]]`：写后者会让 `stmt.where(...)`（返回 `Self`）
        # 被判定成不兼容，同时 `session.scalars(stmt)` 被推成 `tuple[Model]`，
        # 于是一个单纯的排序查询报出两处「类型不对」。
        stmt: Select[TemplatePack] = select(TemplatePack).where(
            TemplatePack.status == "published",
            TemplatePack.version > 0,
            TemplatePack.deleted_at.is_(None),
        )
        if kind and kind != "all":
            stmt = stmt.where(TemplatePack.kind == kind)
        stmt = stmt.order_by(TemplatePack.published_at.desc(), TemplatePack.id)
        return (await self.session.scalars(stmt)).all()

    async def references(self, pack_id: str) -> Sequence[TemplatePackReference]:
        """逐图引用，按 position 升序——这个顺序就是提示词里的编号顺序。"""
        stmt = (
            select(TemplatePackReference)
            .where(TemplatePackReference.pack_id == pack_id)
            .order_by(TemplatePackReference.position)
        )
        return (await self.session.scalars(stmt)).all()

    async def replace_references(
        self, pack_id: str, asset_id_space: str, refs: list[tuple[str, int, str, str]]
    ) -> None:
        """整组覆盖：先删后插。

        为什么不逐条 diff：参考图最多 10 张，整组覆盖的成本远低于「算出增删改」
        的复杂度，而且它天然保证 `position` 连续——`position` 一旦断号，
        提示词里的 `reference-1` / `reference-3` 就会跳号。

        `asset_id_space` 必填且整组同一个：一次封装只属于一套素材库，
        混用的行会让发布期 `_reference_space()` 报错，而错误发生在「发布」，
        离「保存」很远——用户会觉得是自己点错了。
        """
        await self.session.execute(
            delete(TemplatePackReference).where(TemplatePackReference.pack_id == pack_id)
        )
        for asset_id, position, role, description in refs:
            self.session.add(
                TemplatePackReference(
                    pack_id=pack_id,
                    asset_id=asset_id,
                    asset_id_space=asset_id_space,
                    position=position,
                    role=role,
                    description=description,
                )
            )

    async def add(self, pack: TemplatePack) -> TemplatePack:
        self.session.add(pack)
        await self.session.flush()
        return pack

    async def save(self, pack: TemplatePack) -> None:
        await self.session.flush()

    # ---------- 版本 ----------
    async def get_version(self, pack_id: str, version: int) -> TemplatePackVersion | None:
        stmt = select(TemplatePackVersion).where(
            TemplatePackVersion.pack_id == pack_id,
            TemplatePackVersion.version == version,
        )
        return await self.session.scalar(stmt)

    async def list_versions(self, pack_id: str) -> Sequence[TemplatePackVersion]:
        stmt = (
            select(TemplatePackVersion)
            .where(TemplatePackVersion.pack_id == pack_id)
            .order_by(TemplatePackVersion.version.desc())
        )
        return (await self.session.scalars(stmt)).all()

    async def create_version(
        self, pack_id: str, start: int, build: VersionBuilder
    ) -> int:
        """抢一个没人占用的版号，靠 `UNIQUE(pack_id, version)` 做 CAS。

        与旧实现（`storage.create()` 的 `If-None-Match: *`）语义完全一致：
        两个实例同时想写 v3，数据库只让一个成功，另一个拿到 IntegrityError
        后试 v4。区别是不再需要「抢版本文件」这种绕法，也不可能留下
        「两份内容不同却同版本号」的包。

        **每轮重试走 savepoint**：撞唯一约束时回滚的只能是这一次插入。
        直接 `session.rollback()` 会把调用方同一事务里已经写好的封装 head
        一起回滚掉——那是「发布失败」变成「草稿也没了」。
        """
        version = start
        for _ in range(5):
            row = build(version)
            try:
                async with self.session.begin_nested():
                    self.session.add(row)
            except IntegrityError:
                version += 1
                continue
            return version
        raise Conflict("发布冲突过于频繁，请稍后刷新重试")


# 版本行构造函数：`create_version` 需要为每次重试生成一个新的待插入对象
VersionBuilder = Callable[[int], TemplatePackVersion]


# --------------------------------------------------------------------------- #
# 替换运行
# --------------------------------------------------------------------------- #
class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, run_id: str) -> ReplacementRun | None:
        return await self.session.get(ReplacementRun, run_id)

    async def get_or_404(self, run_id: str, *, owner: str | None = None) -> ReplacementRun:
        run = await self.get(run_id)
        if run is None:
            raise NotFound("调用档案不存在")
        if owner is not None and run.owner_id != owner:
            raise NotFound("调用档案不存在")
        return run

    async def list_owned(
        self, owner: str, *, include_deleted: bool = False
    ) -> Sequence[ReplacementRun]:
        stmt = select(ReplacementRun).where(ReplacementRun.owner_id == owner)
        if not include_deleted:
            stmt = stmt.where(ReplacementRun.deleted_at.is_(None))
        stmt = stmt.order_by(ReplacementRun.created_at.desc(), ReplacementRun.id)
        return (await self.session.scalars(stmt)).all()

    async def list_unfinished(self, statuses: Sequence[str]) -> Sequence[ReplacementRun]:
        """启动恢复要扫的行：只取非终态，避免把十年历史全读一遍。"""
        stmt = (
            select(ReplacementRun)
            .where(
                ReplacementRun.status.in_(list(statuses)),
                ReplacementRun.deleted_at.is_(None),
            )
            .order_by(ReplacementRun.created_at)
        )
        return (await self.session.scalars(stmt)).all()

    async def add(self, run: ReplacementRun) -> ReplacementRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def save(self, run: ReplacementRun) -> None:
        await self.session.flush()

    # ---------- 输入 ----------
    async def inputs(self, run_id: str) -> Sequence[ReplacementRunInput]:
        stmt = (
            select(ReplacementRunInput)
            .where(ReplacementRunInput.run_id == run_id)
            .order_by(ReplacementRunInput.position)
        )
        return (await self.session.scalars(stmt)).all()

    async def replace_inputs(self, run_id: str, rows: Sequence[RunInputRow]) -> None:
        await self.session.execute(
            delete(ReplacementRunInput).where(ReplacementRunInput.run_id == run_id)
        )
        for row in rows:
            self.session.add(
                ReplacementRunInput(
                    run_id=run_id,
                    asset_id=row.asset_id,
                    asset_id_space=row.asset_id_space,
                    role=row.role,
                    position=row.position,
                    usage=row.usage,
                    storage_key=row.storage_key,
                )
            )

    # ---------- 评价 ----------
    async def review(self, run_id: str) -> ReplacementReview | None:
        stmt = select(ReplacementReview).where(ReplacementReview.run_id == run_id)
        return await self.session.scalar(stmt)

    async def upsert_review(self, review: ReplacementReview) -> ReplacementReview:
        self.session.add(review)
        await self.session.flush()
        return review


# --------------------------------------------------------------------------- #
# 素材引用：删除素材前「还有谁在用」
# --------------------------------------------------------------------------- #
async def packs_referencing(
    session: AsyncSession, asset_ids: Sequence[str]
) -> dict[str, list[str]]:
    """哪些封装的 head 里还引用着这些素材。

    **只查 head，不查历史版本**：`template_pack_versions` 是冻结证据，每次发布
    都留一份；把它算成引用会让几乎所有老素材永远删不掉。历史版本的可用性由
    `app doctor` 的镜像/字节检查负责报告，不进删除拦截。

    返回 `{asset_id: ["封装《名字》(id)", ...]}`，直接嵌进「不能删除」的报错里。
    """
    wanted = {asset_id for asset_id in asset_ids if asset_id}
    if not wanted:
        return {}
    stmt = (
        select(TemplatePackReference.asset_id, TemplatePack.name, TemplatePack.id)
        .join(TemplatePack, TemplatePack.id == TemplatePackReference.pack_id)
        .where(
            TemplatePackReference.asset_id.in_(list(wanted)),
            TemplatePack.deleted_at.is_(None),
        )
    )
    found: dict[str, list[str]] = {}
    for asset_id, name, pack_id in await session.execute(stmt):
        label = f"封装《{name or '未命名'}》({pack_id})"
        found.setdefault(asset_id, []).append(label)
    return found


async def count_published(session: AsyncSession) -> int:
    stmt = select(func.count()).select_from(TemplatePack).where(
        TemplatePack.status == "published",
        and_(TemplatePack.version > 0, TemplatePack.deleted_at.is_(None)),
    )
    return int(await session.scalar(stmt) or 0)
