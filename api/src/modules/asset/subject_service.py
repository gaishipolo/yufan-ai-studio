"""主体的读写服务：装配后的视图、发布前置、版本冻结。

## 为什么"可用性"要在这里算，不交给前端

`availability` 由三件事决定：成员有没有被删、角色齐不齐、能力够不够。三者的
判定规则都在后端（`subject_capabilities` 是纯函数），前端复算一遍必然漂移——
而漂移的表现是"卡片显示可发布，点发布报缺少能力"，那是最伤信任的一种不一致。

## 发布为什么是"冻结 + CAS"而不是"打标记"

模板按 `version` 缓存提示词与编号顺序。只把 `status` 改成 published 而不新建版本，
会让"发布"与"再保存一次"在模板侧无法区分——缓存要么永不失效（改了不生效），
要么每次都失效（等于没缓存）。新建一行版本 + `UNIQUE(subject_id, version)` 是最小
的、能被数据库原子保证的 CAS。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from modules.asset.models import Asset
from modules.asset.subject_capabilities import MemberView, check_reachability
from modules.asset.subject_models import (
    Subject,
    SubjectLegacyPack,
    SubjectMember,
    SubjectVersion,
)

__all__ = ["SubjectService", "compliance_note"]

#: 合规状态的人话标签。缺一个词时回原值——宁可露出英文枚举，也不要显示空白
_COMPLIANCE_LABELS = {
    "pending": "待审",
    "review_needed": "待人工复核",
    "rejected": "已驳回",
}


def compliance_note(blocked: Sequence[tuple[str, str]]) -> str:
    """把 `[(asset_id, 状态)]` 说成一句能行动的话：几张、哪一档。

    只说"合规未通过"会让人去猜是自己这张有问题还是整组；报数量与档位
    才能决定"去审核"还是"换一张"。
    """
    counts: dict[str, int] = {}
    for _, status in blocked:
        counts[status] = counts.get(status, 0) + 1
    parts = [
        f"{_COMPLIANCE_LABELS.get(status, status)} {count} 张"
        for status, count in counts.items()
    ]
    return f"成员图合规未通过：{'、'.join(parts)}，请在素材详情里处理"


class SubjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 读

    async def list_visible(
        self,
        *,
        owner_id: str,
        visible_to_all: bool,
        subject_type: str = "",
        page: int = 1,
        page_size: int = 24,
    ) -> tuple[list[Subject], int]:
        """我能看到的主体：自己的 + 公共库里已发布的。

        `visible_to_all` 由 `asset:team:manage` 决定（与团队库同一条规则）。
        团队库的主体这一批先不列——跨团队复制是 M5 的事，现在列出来只能看不能用。
        """
        conditions: list[ColumnElement[bool]] = [Subject.archived_at.is_(None)]
        if not visible_to_all:
            conditions.append(Subject.owner_id == owner_id)
        if subject_type:
            conditions.append(Subject.subject_type == subject_type)
        total = await self.session.scalar(
            select(func.count()).select_from(select(Subject.id).where(*conditions).subquery())
        )
        rows = list(
            (
                await self.session.scalars(
                    select(Subject)
                    .where(*conditions)
                    .order_by(Subject.updated_at.desc(), Subject.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return rows, int(total or 0)

    async def get_or_404(self, subject_id: str) -> Subject:
        row = await self.session.get(Subject, subject_id)
        if row is None or row.archived_at is not None:
            raise NotFound("主体不存在或已下架")
        return row

    async def members_of(self, subject_id: str) -> list[SubjectMember]:
        return list(
            (
                await self.session.scalars(
                    select(SubjectMember)
                    .where(SubjectMember.subject_id == subject_id)
                    .order_by(SubjectMember.sort_order)
                )
            ).all()
        )

    async def members_by_pack(self, pack_id: str) -> tuple[Subject, list[SubjectMember]]:
        """旧 `packId` → 主体。B5：历史链接与外部系统还在用这个 id。"""
        subject_id = await self.session.scalar(
            select(SubjectLegacyPack.subject_id).where(SubjectLegacyPack.pack_id == pack_id)
        )
        if not subject_id:
            raise NotFound("这个旧素材包没有对应的主体")
        subject = await self.get_or_404(subject_id)
        return subject, await self.members_of(subject.id)

    # -------------------------------------------------------------- 判定

    async def member_info(self, asset_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
        """成员素材的展示信息：`{asset_id: (名称, 封面)}`。

        **选择"一次查完"而不是让前端逐个取**：成员条带最多 10 条，前端逐个取
        就是 10 个请求，而它们总是一起出现、一起消失。查不到的行给空串——
        已删的素材仍要占一个位置（那是"这里缺一张图"的证据）。
        """
        if not asset_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Asset.id, Asset.name, Asset.cover_url).where(Asset.id.in_(asset_ids))
            )
        ).all()
        return {asset_id: (name, cover) for asset_id, name, cover in rows}

    async def member_views(self, members: Sequence[SubjectMember]) -> list[MemberView]:
        """把 ORM 行转成判定视图，并补上"素材是否已删"。

        已删的素材**不改成员行**：删主体不删成员（铁律①），反过来删素材也不该
        静默改主体——主体要能显示"这里缺一张图"，而不是悄悄少一张。
        """
        if not members:
            return []
        ids = [member.asset_id for member in members]
        rows = (
            await self.session.execute(
                select(Asset.id, Asset.deleted_at).where(Asset.id.in_(ids))
            )
        ).all()
        deleted = {asset_id: True for asset_id, deleted_at in rows if deleted_at is not None}
        existing = {asset_id for asset_id, _ in rows}
        views = [
            MemberView(
                asset_id=member.asset_id,
                role=member.role,
                grants=tuple(member.grants or ()),
                in_output=member.in_output,
                # 素材行不存在（被彻底删了）同样算不可用
                deleted=deleted.get(member.asset_id, False)
                or member.asset_id not in existing,
            )
            for member in members
        ]
        return views

    async def reachability(self, subject: Subject, members: Sequence[SubjectMember]):
        views = await self.member_views(members)
        return check_reachability(
            views,
            requires=list(subject.requires or []),
            required_roles=list(subject.required_roles or []),
        )

    async def availability(
        self, subject: Subject, members: Sequence[SubjectMember]
    ) -> tuple[str, str]:
        """`(状态, 一句话)`。见 `subject_schema` 里三态的定义。

        合规也在这里算：它在 `publish()` 里是一道硬前置，如果这里不算，
        卡片会显示"可发布"、点下去报"合规未通过"——正是这个模块最想避免的那种
        不一致（判定规则必须在同一处）。
        """
        if not members:
            return "incomplete", "还没有成员"
        result = await self.reachability(subject, members)
        if result.broken_members:
            return "broken", f"{len(result.broken_members)} 张成员图已被删除"
        if not result.ok:
            return "incomplete", result.why()
        blocked = await self.compliance_blocked(members)
        if blocked:
            return "incomplete", compliance_note(blocked)
        if subject.status != "published":
            return "incomplete", "尚未发布"
        return "ready", ""

    async def compliance_blocked(
        self, members: Sequence[SubjectMember]
    ) -> list[tuple[str, str]]:
        """成员里合规不通过的 `[(asset_id, 状态)]`。

        只认 `approved`：`pending`（还没审）与 `review_needed`（要人工看一眼）
        都不是"通过了"，把它们放行等于让未审核的 AI 产物对外发布。
        上传件的 `compliance_status` 恒为 `approved`（B2 只审 AI 产物），
        所以这条规则不会挡住人上传的图。
        """
        ids = [member.asset_id for member in members]
        if not ids:
            return []
        rows = (
            await self.session.execute(
                select(Asset.id, Asset.compliance_status).where(Asset.id.in_(ids))
            )
        ).all()
        return [
            (asset_id, status or "pending")
            for asset_id, status in rows
            if status != "approved"
        ]

    # -------------------------------------------------------------- 发布

    async def publish(self, subject_id: str, *, actor: str) -> SubjectVersion:
        """发布：过两条可达性 + 至少一个成员 + 冻结版本。

        三道前置按"用户最容易弄错"的顺序排：先看有没有图（空主体最常见），
        再看角色，最后看能力。顺序反了会让一个空主体报"缺少能力 generate"，
        而用户盯着那个词完全不知道要先传图。
        """
        subject = await self.get_or_404(subject_id)
        members = await self.members_of(subject.id)
        if not members:
            raise BadRequest("至少要有一张成员图才能发布")
        result = await self.reachability(subject, members)
        if result.broken_members:
            # 缺图不是"配置错误"而是"数据不完整"，所以给的是 409 而不是 400：
            # 请求本身没问题，是当前状态不允许。
            raise Conflict(f"有 {len(result.broken_members)} 张成员图已被删除，请先补齐")
        if not result.ok:
            raise BadRequest(result.why())
        blocked = await self.compliance_blocked(members)
        if blocked:
            # 合规未过是 400 而不是 409：请求本身不该被发（产物还不允许公开），
            # 而 409 的意思是"状态冲突、等一会重试"——这里等多久都不会自己变好
            raise BadRequest(compliance_note(blocked))

        version = subject.current_version + 1
        frozen = await self._freeze(subject, members, version, actor=actor)
        subject.current_version = version
        subject.status = "published"
        subject.published_at = datetime.now(UTC)
        subject.revision = subject.revision + 1
        self.session.add(frozen)
        # 这一批的写路径只有发布一个动作，事务边界跟着它走：
        # 版本行与主体的 `current_version` 必须在同一个事务里落地，否则
        # 中途失败会留下"版本 3 存在、主体还指向 2"的错位，而模板按
        # `current_version` 取图，读到的就是老图——比直接报错更难查。
        await self.session.commit()
        return frozen

    async def _freeze(
        self, subject: Subject, members: Sequence[SubjectMember], version: int, *, actor: str
    ) -> SubjectVersion:
        ids = [member.asset_id for member in members]
        digests = dict(
            (
                await self.session.execute(select(Asset.id, Asset.sha256).where(Asset.id.in_(ids)))
            ).all()
        )
        return SubjectVersion(
            subject_id=subject.id,
            version=version,
            members=[
                {
                    "asset_id": member.asset_id,
                    # 摘要在版本里留一份：素材被换掉内容时，历史版本仍能证明
                    # "当时用的是哪一份字节"
                    "sha256": digests.get(member.asset_id) or "",
                    "role": member.role,
                    "grants": list(member.grants or ()),
                    "in_output": member.in_output,
                    "sort_order": member.sort_order,
                }
                for member in members
            ],
            cover_asset_id=subject.cover_asset_id,
            attributes=dict(subject.attributes or {}),
            requires=list(subject.requires or []),
            required_roles=list(subject.required_roles or []),
            published_at=datetime.now(UTC),
            published_by=actor,
        )

    # -------------------------------------------------------------- 校验

    async def owned(self, subject_id: str, actor: str, *, can_manage_all: bool) -> Subject:
        subject = await self.get_or_404(subject_id)
        if subject.owner_id != actor and not can_manage_all:
            # 别人的主体与不存在的 id 同一个错误：区分开来等于给出"这个 id 存在"的信息
            raise Forbidden("只能管理自己组装的主体")
        return subject
