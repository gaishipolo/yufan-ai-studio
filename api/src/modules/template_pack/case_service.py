"""模板案例的服务层：发布、列表、编辑、下架、上架、删除、恢复。

## 权限与状态规则（**只在这里定义一次**，别处引用，不重复实现）

| 动作 | 权限码 | 附加条件 |
|---|---|---|
| 看案例库 / 读案例 | `template_pack:case:read` | 已发布的对有 read 的人可见 |
| 发布为案例 | `template_pack:case:write` + `template_pack:publish` | 必须是**源模板的作者** |
| 编辑 / 下架 / 上架 / 删除 / 恢复 | `template_pack:case:write` | 必须是**案例的作者** |

「下架」与「回收站」里的案例只有作者自己看得到；已发布的案例对所有有 read 的人
可见 —— 别人要能拿它复用。

「发布案例」额外要求 `template_pack:publish`：把配置公开给所有人，与「发布模板」
是同一类对外动作。只给案例写码就等于给了一条绕过模板发布闸口的路。

「作者」判定与模板完全一致（`spec_service.save` / `publish` 只允许本人操作）：
否则任何一个有写权限的人都能改别人的案例，案例库会变成公共涂鸦板。

读不到时回 **404 而不是 403** —— 403 会确认「这个案例存在」，等于给 id 空间
开了枚举口（与 `spec_service.get_out` 同理）。
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from core.ports.library_lookup import get_library_lookup
from core.ports.org_directory import user_names
from db.base import utcnow
from modules.template_pack.case_schema import (
    TemplateCaseCreateIn,
    TemplateCaseListOut,
    TemplateCaseOut,
    TemplateCaseUpdateIn,
)
from modules.template_pack.models import TemplateCase
from modules.template_pack.models import TemplateSpec as SpecRow
from modules.template_pack.spec_schema import TemplateSpecIn
from modules.template_pack.spec_service import validate_draft

__all__ = [
    "publish_case",
    "list_cases",
    "get_case",
    "update_case",
    "offline_case",
    "online_case",
    "delete_case",
    "restore_case",
]


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
async def _load(session: AsyncSession, case_id: str) -> TemplateCase:
    row = await session.get(TemplateCase, case_id)
    if row is None:
        raise NotFound(f"案例不存在：{case_id}")
    return row


def _require_author(row: TemplateCase, actor: CurrentUser) -> None:
    """案例的写操作只允许作者本人（与模板同一套规矩）。"""
    if row.owner_id != actor.id:
        raise Conflict("只能操作自己发布的案例")


def _out(
    row: TemplateCase,
    *,
    spec_name: str,
    owner_name: str,
    source_missing: bool = False,
    config: dict | None = None,
) -> TemplateCaseOut:
    return TemplateCaseOut(
        id=row.id,
        name=row.name,
        description=row.description,
        category=row.category,
        status=row.status,
        spec_id=row.spec_id,
        spec_key=row.spec_key,
        spec_name=spec_name,
        spec_version=row.spec_version,
        source_missing=source_missing,
        config=config if config is not None else (row.config or {}),
        owner_id=row.owner_id,
        owner_name=owner_name,
        published_at=row.published_at,
        offline_at=row.offline_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _names(
    session: AsyncSession, rows: list[TemplateCase]
) -> list[tuple[TemplateCase, str, str, bool]]:
    """补齐出参需要的三个派生字段：模板名、发布者名、来源模板是否已删。

    一次查两批，不在循环里查库。
    """
    if not rows:
        return []

    spec_ids = {row.spec_id for row in rows}
    spec_rows = (
        await session.scalars(select(SpecRow).where(SpecRow.id.in_(spec_ids)))
    ).all()
    spec_map = {spec.id: spec for spec in spec_rows}

    owner_ids = {row.owner_id for row in rows}
    # 用户名走**组织目录端口**，不直接查 identity 的表：业务模块之间不互相
    # import（架构守卫盯着这条），组织模型是 identity 的地盘。
    owner_map = await user_names(session, list(owner_ids))

    result: list[tuple[TemplateCase, str, str, bool]] = []
    for row in rows:
        spec = spec_map.get(row.spec_id)
        # 源模板被软删：案例自带快照，照样能复用，只是要告诉用户「母版没了」。
        missing = spec is None or spec.deleted_at is not None
        spec_name = spec.name if spec is not None else row.spec_key
        owner_name = owner_map.get(row.owner_id, "")
        result.append((row, spec_name, owner_name, missing))
    return result


async def _out_of(
    session: AsyncSession, actor: CurrentUser, row: TemplateCase
) -> TemplateCaseOut:
    pairs = await _names(session, [row])
    _, spec_name, owner_name, missing = pairs[0]
    configs = await _sanitize_configs(session, actor, [row])
    return _out(
        row,
        spec_name=spec_name,
        owner_name=owner_name,
        source_missing=missing,
        config=configs.get(row.id),
    )


async def _sanitize_configs(
    session: AsyncSession, actor: CurrentUser, rows: list[TemplateCase]
) -> dict[str, dict]:
    """把**请求者读不到**的固定素材引用从快照里摘掉（批量，一次可读性查询）。

    案例是**别人也能复用**的东西：作者可能在固定槽位上用了一条自己可见、
    别人看不见的素材。快照原样带过去，复用者一存就撞「固定素材不可用或无权
    访问」—— 复用的意义是拿到一份能接着改的起点，不该在半路就卡死。

    摘掉的是**素材引用**，不是槽位：槽位结构（位置、提示词里的编号引用）原样
    保留，复用者在编辑器里给自己挑一张就能接着用。

    作者自己看自己的案例时什么都摘不掉（他本来就读得到）。
    """
    if not rows:
        return {}

    asset_ids: list[str] = []
    for row in rows:
        slots = (row.config or {}).get("slots")
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            # 快照里的槽位字段是**驼峰**（`assetId`），与前端契约一致；
            # 老数据可能是蛇形，两个都认。
            asset_id = slot.get("assetId") or slot.get("asset_id")
            if asset_id:
                asset_ids.append(str(asset_id))

    missing: set[str] = set()
    if asset_ids:
        lookup = get_library_lookup()
        readable = await lookup.readable_ids(session, actor, asset_ids)
        missing = {item for item in asset_ids if item not in readable}

    result: dict[str, dict] = {}
    for row in rows:
        config = dict(row.config or {})
        slots = config.get("slots")
        if missing and isinstance(slots, list):
            cleaned_slots: list[object] = []
            for slot in slots:
                if not isinstance(slot, dict):
                    cleaned_slots.append(slot)
                    continue
                asset_id = slot.get("assetId") or slot.get("asset_id")
                if not asset_id or str(asset_id) not in missing:
                    cleaned_slots.append(slot)
                    continue
                # 摘掉**引用**，槽位本身留着：位置、提示词里的编号引用都不动。
                cleaned = dict(slot)
                if "assetId" in cleaned:
                    cleaned["assetId"] = None
                if "asset_id" in cleaned:
                    cleaned["asset_id"] = None
                cleaned_slots.append(cleaned)
            config["slots"] = cleaned_slots
        result[row.id] = config
    return result


# --------------------------------------------------------------------------- #
# 发布
# --------------------------------------------------------------------------- #
async def publish_case(
    session: AsyncSession, actor: CurrentUser, payload: TemplateCaseCreateIn
) -> TemplateCaseOut:
    """把一份模板发布成可复用的案例。

    快照取的是模板**当前草稿** —— 不是上一次发版的那份。理由：发布案例这个
    动作本身就是一次对外发布，作者要的是**眼前这份**配置；他改完还没发版就
    发布案例，拿到的却是旧内容，那才叫意外。

    发布前按「可执行」标准预检一遍：发布出去的配置如果跑不起来，复用者拿到
    的是一份坏起点。
    """
    head = await session.scalar(
        select(SpecRow).where(
            SpecRow.spec_key == payload.spec_key, SpecRow.deleted_at.is_(None)
        )
    )
    if head is None:
        raise NotFound(f"模板不存在：{payload.spec_key}")
    if head.owner_id != actor.id:
        raise Conflict(f"模板 {payload.spec_key} 属于其他用户，不能发布为案例")
    if not head.draft:
        raise BadRequest("模板还没有保存任何内容，无法发布为案例")

    same_name = await session.scalar(
        select(func.count())
        .select_from(TemplateCase)
        .where(
            TemplateCase.owner_id == actor.id,
            TemplateCase.name == payload.name,
            TemplateCase.deleted_at.is_(None),
        )
    )
    if same_name:
        raise Conflict(f"已有同名案例：{payload.name}")

    spec_payload = TemplateSpecIn.model_validate(head.draft)
    await validate_draft(session, actor, spec_payload, executable=True)

    # 存草稿时 `revision` 已经被 exclude 掉（见 `spec_service.save`），这里拷一份
    # 出来只当快照用。快照里带着源模板的 `key` 与 `name` —— 复用时**要换成新的**
    # （模板 key 全局唯一，复用一份案例不该抢走源模板的 key）。
    config = dict(head.draft or {})

    row = TemplateCase(
        name=payload.name,
        description=payload.description,
        category=payload.category,
        spec_id=head.id,
        spec_key=head.spec_key,
        spec_version=head.current_version,
        config=config,
        status="published",
        owner_id=actor.id,
        published_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    await session.commit()
    await session.refresh(row)
    return await _out_of(session, actor, row)


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
async def list_cases(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    scope: str = "library",
    status: str = "",
    category: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 50,
) -> TemplateCaseListOut:
    """列出案例。

    `scope` 三档：

    - `library`（默认）：**公共案例库**。只有已发布的，且不带回收站里的。
    - `mine`：我发布的全部（含已下架），用来管理自己的案例。
    - `trash`：回收站。

    `total` 与 `items` 必须是同一个口径 —— 分开算就会翻出「共 12 条、第 2 页空的」。
    """
    stmt = select(TemplateCase)
    count_stmt = select(func.count()).select_from(TemplateCase)

    if scope == "trash":
        stmt = stmt.where(
            TemplateCase.deleted_at.is_not(None), TemplateCase.owner_id == actor.id
        )
        count_stmt = count_stmt.where(
            TemplateCase.deleted_at.is_not(None), TemplateCase.owner_id == actor.id
        )
    elif scope == "mine":
        stmt = stmt.where(
            TemplateCase.deleted_at.is_(None), TemplateCase.owner_id == actor.id
        )
        count_stmt = count_stmt.where(
            TemplateCase.deleted_at.is_(None), TemplateCase.owner_id == actor.id
        )
    else:
        stmt = stmt.where(
            TemplateCase.deleted_at.is_(None), TemplateCase.status == "published"
        )
        count_stmt = count_stmt.where(
            TemplateCase.deleted_at.is_(None), TemplateCase.status == "published"
        )

    if status:
        stmt = stmt.where(TemplateCase.status == status)
        count_stmt = count_stmt.where(TemplateCase.status == status)
    if category:
        stmt = stmt.where(TemplateCase.category == category)
        count_stmt = count_stmt.where(TemplateCase.category == category)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                TemplateCase.name.ilike(like),
                TemplateCase.description.ilike(like),
                TemplateCase.spec_key.ilike(like),
            )
        )
        count_stmt = count_stmt.where(
            or_(
                TemplateCase.name.ilike(like),
                TemplateCase.description.ilike(like),
                TemplateCase.spec_key.ilike(like),
            )
        )

    total = int(await session.scalar(count_stmt) or 0)

    stmt = (
        stmt.order_by(TemplateCase.published_at.desc(), TemplateCase.created_at.desc())
        .offset(max(0, page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.scalars(stmt)).all())

    # 分类清单给筛选器用：口径与列表一致（同一批可见案例的分类）。
    category_stmt = select(TemplateCase.category).distinct()
    if scope == "library":
        category_stmt = category_stmt.where(
            TemplateCase.deleted_at.is_(None), TemplateCase.status == "published"
        )
    else:
        category_stmt = category_stmt.where(
            TemplateCase.deleted_at.is_(None), TemplateCase.owner_id == actor.id
        )
    categories = [
        value
        for value in (await session.scalars(category_stmt)).all()
        if value
    ]

    pairs = await _names(session, rows)
    configs = await _sanitize_configs(session, actor, rows)
    items = [
        _out(
            row,
            spec_name=spec_name,
            owner_name=owner_name,
            source_missing=missing,
            config=configs.get(row.id),
        )
        for row, spec_name, owner_name, missing in pairs
    ]
    return TemplateCaseListOut(items=items, total=total, categories=sorted(categories))


async def get_case(
    session: AsyncSession, actor: CurrentUser, case_id: str
) -> TemplateCaseOut:
    """案例详情。

    已发布的案例是**共享资源**（别人要能拿它复用）；下架与回收站里的只有
    作者自己看得到。越权回 404 而不是 403。
    """
    row = await _load(session, case_id)
    if row.status != "published" and row.owner_id != actor.id:
        raise NotFound(f"案例不存在：{case_id}")
    return await _out_of(session, actor, row)


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
async def update_case(
    session: AsyncSession,
    actor: CurrentUser,
    case_id: str,
    payload: TemplateCaseUpdateIn,
) -> TemplateCaseOut:
    """编辑案例。**只改案例自己的字段**（名称/描述/分类），不碰模板。"""
    row = await _load(session, case_id)
    _require_author(row, actor)

    if payload.name is not None and payload.name != row.name:
        same_name = await session.scalar(
            select(func.count())
            .select_from(TemplateCase)
            .where(
                TemplateCase.owner_id == actor.id,
                TemplateCase.name == payload.name,
                TemplateCase.deleted_at.is_(None),
                TemplateCase.id != row.id,
            )
        )
        if same_name:
            raise Conflict(f"已有同名案例：{payload.name}")
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.category is not None:
        row.category = payload.category

    await session.commit()
    await session.refresh(row)
    return await _out_of(session, actor, row)


async def offline_case(
    session: AsyncSession, actor: CurrentUser, case_id: str
) -> TemplateCaseOut:
    """下架：案例不再出现在复用选择器里，但管理界面仍然看得到，也能重新上架。"""
    row = await _load(session, case_id)
    _require_author(row, actor)
    if row.status != "offline":
        row.status = "offline"
        row.offline_at = utcnow()
        await session.commit()
        await session.refresh(row)
    return await _out_of(session, actor, row)


async def online_case(
    session: AsyncSession, actor: CurrentUser, case_id: str
) -> TemplateCaseOut:
    """重新上架。"""
    row = await _load(session, case_id)
    _require_author(row, actor)
    if row.status != "published":
        row.status = "published"
        row.offline_at = None
        await session.commit()
        await session.refresh(row)
    return await _out_of(session, actor, row)


async def delete_case(
    session: AsyncSession, actor: CurrentUser, case_id: str
) -> TemplateCaseOut:
    """删除（软删）。进回收站，可恢复 —— 与模板同一套语义。

    案例没有被别的表引用，物理删除也不会留悬空引用；这里仍然软删，是为了
    「误删能救回来」以及与模板的删除体验一致。
    """
    row = await _load(session, case_id)
    _require_author(row, actor)
    if row.deleted_at is None:
        row.deleted_at = utcnow()
        await session.commit()
        await session.refresh(row)
    return await _out_of(session, actor, row)


async def restore_case(
    session: AsyncSession, actor: CurrentUser, case_id: str
) -> TemplateCaseOut:
    """从回收站恢复。下架状态原样保留 —— 恢复 ≠ 上架。"""
    row = await _load(session, case_id)
    _require_author(row, actor)
    if row.deleted_at is not None:
        row.deleted_at = None
        await session.commit()
        await session.refresh(row)
    return await _out_of(session, actor, row)
