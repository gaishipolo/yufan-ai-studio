"""已发布主体的成员授权查询（M3 起被 `download` / `derive` 两个判定使用）。

## 为什么单独一个文件

`SubjectService` 关心的是"这个主体能不能发布"（成员齐、角色可达、能力可达），
而这里关心的是"**这张图**在对外承诺里被允许做什么"。两件事共用一个类会让
`SubjectService` 多出四个与发布无关的方法，而调用方（素材出参、衍生入口）
只需要其中一个判定。

## "不在任何已发布主体里"等于"不受限"

成员授权是**对外承诺**：主体一旦发布，它就代表"这几张图可以被这样用"。
一张没进过任何已发布主体的素材没有承诺可违反，所以按归属判定即可
（本人可下载、可衍生）。这也是为什么返回字典里**缺席表示无约束**，
而不是补一个"全都允许"的默认集合——空集等于"全都禁止"，含义正好相反。

## 多主体取并集

`download` / `derive` 是**逐成员**判的（木桶不压整体）：同一张图在主体 A 里
允许下载、在主体 B 里不允许，那它整体上仍可下载——B 的承诺只约束 B 的消费者。
`generate` 走的是主体级交集，不在这个文件的职责里（见 `subject_capabilities.py`）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.asset.subject_models import Subject, SubjectMember

__all__ = ["allows", "published_grants"]


async def published_grants(
    session: AsyncSession, asset_ids: Sequence[str]
) -> dict[str, set[str]]:
    """每张素材在已发布主体里被授予的能力（多主体并集）。缺席 = 不是成员。"""
    ids = [asset_id for asset_id in asset_ids if asset_id]
    if not ids:
        return {}
    rows = await session.execute(
        select(SubjectMember.asset_id, SubjectMember.grants)
        .join(Subject, Subject.id == SubjectMember.subject_id)
        # 只看 published：草稿是"组装中"，它不构成对外承诺。
        # 归档的主体也不看——下架之后就不该继续约束别人的素材。
        .where(
            SubjectMember.asset_id.in_(ids),
            Subject.status == "published",
            Subject.archived_at.is_(None),
        )
    )
    grants: dict[str, set[str]] = {}
    for asset_id, values in rows.all():
        grants.setdefault(asset_id, set()).update(values or ())
    return grants


def allows(grants: dict[str, set[str]], asset_id: str, capability: str) -> bool:
    """这张素材是否被允许做 `capability`。不是任何主体的成员时恒为真。"""
    current = grants.get(asset_id)
    if current is None:
        return True
    return capability in current
