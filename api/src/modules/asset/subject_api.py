"""主体路由：列表、详情、旧包解析、发布。

发布是这一批唯一的**写**动作。组装/编辑（拖图、改角色、调顺序）留到 M3.5：
组装台要按模板槽位才知道"该按哪些角色摆"，现在做会白做一遍——而"发布"
不依赖槽位，它只依赖可达性判定，所以可以提前落地并先把闸门立住。

权限沿用 `asset:read` / `asset:update`，不为发布新造一个码：发布改的是
**本人组装的素材**，`asset:update` 的语义边界（改自己的东西）正好覆盖它；
新码会让每个角色配置界面多一个必须勾、不勾就 403 的选项，而没有任何人
能从名字上分清它和 `asset:update` 的区别。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import SessionDep, rate_limit, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.asset.subject_models import Subject, SubjectMember
from modules.asset.subject_schema import (
    SubjectDetail,
    SubjectList,
    SubjectMemberOut,
    SubjectOut,
    SubjectPublishResult,
)
from modules.asset.subject_service import SubjectService

router = APIRouter(prefix="/subjects")

SubjectReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]
SubjectWriteDep = Annotated[CurrentUser, Depends(require("asset:update"))]


def _out(
    subject: Subject, member_count: int, availability: str, note: str, cover_url: str = ""
) -> SubjectOut:
    return SubjectOut(
        id=subject.id,
        name=subject.name,
        subject_type=subject.subject_type,
        status=subject.status,
        visibility=subject.visibility,
        team_id=subject.team_id,
        category_id=subject.category_id,
        cover_asset_id=subject.cover_asset_id,
        cover_url=cover_url,
        member_count=member_count,
        current_version=subject.current_version,
        availability=availability,
        availability_note=note,
    )


async def _detail(
    service: SubjectService, subject: Subject, members: Sequence[SubjectMember]
) -> SubjectDetail:
    """详情公共装配：可用性 + 完整判定结果 + 成员条带。

    `_out` 的出参先 `model_dump()` 再展开，而不是逐个字段重抄一遍：
    列表项与详情项必须字段一致，抄一遍的代价是以后加字段只改了一边，
    症状是"卡上有、点进去没有"。
    """
    state, note = await service.availability(subject, members)
    result = await service.reachability(subject, members)
    # 封面可能不在成员里（历史数据允许），所以把它一起查进来
    wanted = [member.asset_id for member in members]
    if subject.cover_asset_id and subject.cover_asset_id not in wanted:
        wanted.append(subject.cover_asset_id)
    info = await service.member_info(wanted)
    broken = set(result.broken_members)
    base = _out(
        subject, len(members), state, note, info.get(subject.cover_asset_id, ("", ""))[1]
    )
    return SubjectDetail(
        **base.model_dump(),
        members=[
            SubjectMemberOut(
                asset_id=member.asset_id,
                name=info.get(member.asset_id, ("", ""))[0],
                cover_url=info.get(member.asset_id, ("", ""))[1],
                role=member.role,
                sort_order=member.sort_order,
                grants=list(member.grants or ()),
                in_output=member.in_output,
                missing=member.asset_id in broken,
            )
            for member in members
        ],
        attributes=dict(subject.attributes or {}),
        requires=list(subject.requires or []),
        required_roles=list(subject.required_roles or []),
        missing_capabilities=list(result.missing_capabilities),
        missing_roles=list(result.missing_roles),
        member_gaps={asset_id: list(caps) for asset_id, caps in result.member_gaps.items()},
    )


@router.get("", summary="主体列表（带成员数与可用性）")
async def list_subjects(
    actor: SubjectReadDep,
    session: SessionDep,
    subject_type: str = Query(default="", alias="subjectType", max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, alias="pageSize", ge=1, le=100),
) -> ApiResponse[SubjectList]:
    """列表项不带成员明细：一页 24 个主体 × 10 个成员 = 240 条素材塞进一次响应，
    而其中绝大多数不会被看。要看成员就点进详情。

    `availability` 三态在服务端算（`ready` / `broken` / `incomplete`）。
    前端复算必然与后端漂移，症状是"卡片显示可发布、点发布报缺少能力"。
    """
    service = SubjectService(session)
    rows, total = await service.list_visible(
        owner_id=actor.id,
        visible_to_all=actor.has("asset:team:manage"),
        subject_type=subject_type,
        page=page,
        page_size=page_size,
    )
    items = []
    # 封面一次查完：一页 24 个主体逐个反查就是 24 个查询，而它们都是同一种形状
    covers = await service.member_info([row.cover_asset_id for row in rows if row.cover_asset_id])
    for subject in rows:
        members = await service.members_of(subject.id)
        state, note = await service.availability(subject, members)
        cover = covers.get(subject.cover_asset_id, ("", ""))[1]
        items.append(_out(subject, len(members), state, note, cover))
    return ApiResponse.ok(SubjectList(items=items, total=total, page=page, page_size=page_size))


# 必须写在 `/{subject_id}` **之前**：FastAPI 按注册顺序匹配，
# 反过来的话 `/subjects/by-legacy-pack/x` 会被当成 id 吃掉，然后报"主体不存在"。
@router.get("/by-legacy-pack/{pack_id}", summary="按旧素材包 id 解析主体（B5）")
async def by_legacy_pack(
    pack_id: str, actor: SubjectReadDep, session: SessionDep
) -> ApiResponse[SubjectDetail]:
    """旧链接、外部系统、历史工单里全是 `packId`。这条让它们还能打开。

    解析不到是 404 而不是空主体：空主体会让前端渲染一张"什么都是空的"卡片，
    看起来像数据坏了；404 至少说清了"这个 id 没有对应主体"。
    """
    service = SubjectService(session)
    subject, members = await service.members_by_pack(pack_id)
    return ApiResponse.ok(await _detail(service, subject, members))


@router.get("/{subject_id}", summary="主体详情（含成员条带与缺项）")
async def get_subject(
    subject_id: str, actor: SubjectReadDep, session: SessionDep
) -> ApiResponse[SubjectDetail]:
    """详情带成员条带，每条含 `grants` 与 `missing`。

    `memberGaps` 是"到底谁不给哪项能力"的答案。只给主体级结论（缺少 download）
    的话，用户只能一张张点开看——主体最多 10 张，但没人会真的去数。
    """
    service = SubjectService(session)
    subject = await service.get_or_404(subject_id)
    return ApiResponse.ok(await _detail(service, subject, await service.members_of(subject.id)))


@router.post(
    "/{subject_id}/publish",
    summary="发布主体（过可达性后冻结版本）",
    dependencies=[Depends(rate_limit("asset.subjects.publish", limit=30))],
)
async def publish_subject(
    subject_id: str, actor: SubjectWriteDep, session: SessionDep
) -> ApiResponse[SubjectPublishResult]:
    """发布有三道前置，缺一不可：至少一张成员、角色可达、能力可达。

    失败时**不返回 200 + 空结果**：发布是模板侧缓存的失效点，静默失败会让
    "发布了但模板还是老图"变成一个要靠猜的问题。
    """
    service = SubjectService(session)
    await service.owned(subject_id, actor.id, can_manage_all=actor.has("asset:team:manage"))
    frozen = await service.publish(subject_id, actor=actor.id)
    return ApiResponse.ok(
        SubjectPublishResult(
            subject_id=frozen.subject_id,
            version=frozen.version,
            published_at=frozen.published_at.isoformat() if frozen.published_at else "",
        )
    )
