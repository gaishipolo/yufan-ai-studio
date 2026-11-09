"""素材模块对 `SubjectLookup` 注入口的实现（M2.5-C）。

模板在 prepare 阶段要一组按顺序的参考图。改造前它读
`template_pack_versions.reference_ids`；主体落地后权威来源是
`subject_versions.members`，而**旧包 → 主体**的对应关系在
`subject_legacy_packs` 里。

## 为什么读 `subject_versions` 而不是 `subject_members`

run 是按**已发布版本**冻结的（`pack_version` 必填）。读活表会让"发布之后有人
改了成员"影响到已经在跑的任务，而那正是 `subject_versions` 存在的理由：
版本是冻结证据，改成员只改下一版。

## 缺映射怎么办

返回 `None`，不返回空元组。`None` 是"不知道"，由模板侧决定回落旧路径；
空元组会被当成"这个包确实没有图"，于是生成出来的图少了参考——症状是
"结果与上周不一样"，而没人会想到是这里。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.subjects import SubjectLookup, SubjectSnapshot, register_subject_lookup
from modules.asset.subject_capabilities import MemberView, effective_capabilities
from modules.asset.subject_models import SubjectLegacyPack, SubjectVersion

__all__ = ["SqlSubjectLookup", "lookup", "install"]


class SqlSubjectLookup(SubjectLookup):
    async def snapshot(
        self, session: AsyncSession, pack_id: str, version: int
    ) -> SubjectSnapshot | None:
        if not pack_id:
            return None
        subject_id = await session.scalar(
            select(SubjectLegacyPack.subject_id).where(SubjectLegacyPack.pack_id == pack_id)
        )
        if not subject_id:
            return None
        row = await session.scalar(
            select(SubjectVersion).where(
                SubjectVersion.subject_id == subject_id, SubjectVersion.version == version
            )
        )
        if row is None:
            return None

        # `members` 是冻结的 JSON。按 `sort_order` 排一遍而不是信写入顺序：
        # 编号顺序错一位，提示词里的 `reference-2` 就指向了另一张图，
        # 而生成结果看起来只是"风格不太对"。
        members = sorted(
            (m for m in (row.members or []) if m.get("asset_id")),
            key=lambda m: int(m.get("sort_order", 0)),
        )
        # 冻结快照里没有"是否已删"这个事实（删图发生在发布之后）。这里按
        # **可用**处理，由模板侧的资产校验去拦——它本来就有那道检查，
        # 在这里再判一次会让两个地方对"缺图"给出不同答案。
        views = [
            MemberView(
                asset_id=member["asset_id"],
                role=member.get("role", ""),
                grants=tuple(member.get("grants") or ()),
                in_output=bool(member.get("in_output", True)),
            )
            for member in members
        ]
        return SubjectSnapshot(
            subject_id=subject_id,
            version=int(row.version),
            asset_ids=tuple(view.asset_id for view in views),
            roles={view.asset_id: view.role for view in views},
            capabilities=frozenset(effective_capabilities(views)),
        )


lookup = SqlSubjectLookup()


def install() -> None:
    """import 期挂到注入口。幂等——每次传的是同一个 `lookup` 实例。"""
    register_subject_lookup(lookup)
