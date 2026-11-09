"""身份模块的部署自检：`ModuleSpec.doctor` 指向这里。

三层授权表是「没有外键」的设计，删组由 repository 显式清绑定。这就要求
有一条能看见「清理漏了」的检查，否则孤儿 grant 会安静地留在库里：
界面上看不到它，权限解析也不会报错，只有 doctor 能指出那一行。

自检纪律：只往报告里加自己的条目、只读不写。
"""

from __future__ import annotations

from api.module import DoctorReportLike
from core.permissions import catalog

__all__ = ["check"]


async def check(report: DoctorReportLike) -> None:
    from db.session import get_session_factory
    from modules.identity.permissions import (
        SCOPE_TEAM,
        TEAM_FORBIDDEN_PREFIXES,
        TEAM_SCOPE_ID,
    )
    from modules.identity.repository import PermissionLayerRepository

    async with get_session_factory()() as session:
        layers = PermissionLayerRepository(session)
        groups = await layers.list_groups()
        orphans = await layers.orphan_grants({group.id for group in groups})
        team = await layers.grants(SCOPE_TEAM, TEAM_SCOPE_ID)
        stored_codes = await layers.all_grant_codes()

    known = catalog.codes()
    resources = {code.split(":", 1)[0] for code in known}
    stale = sorted(
        code
        for code in stored_codes
        if code != "*"
        and code not in known
        and not (code.endswith(":*") and code[:-2] in resources)
    )
    forbidden = sorted(
        code
        for code in team
        if code == "*" or code.startswith(TEAM_FORBIDDEN_PREFIXES)
    )

    problems: list[str] = []
    if orphans:
        problems.append(f"{len(orphans)} 个小组授权指向已删除的小组 {orphans[:3]}")
    if stale:
        problems.append(f"{len(stale)} 个权限码已下线：{stale[:3]}")
    if forbidden:
        problems.append(f"团队基础权限含管理类/通配码：{forbidden}")

    if problems:
        report.add(
            "identity.permission_layers",
            "WARN",
            "；".join(problems) + "；可在「用户与权限」页重新保存对应层清理",
        )
    else:
        report.add(
            "identity.permission_layers",
            "PASS",
            f"{len(groups)} 个小组、{len(stored_codes)} 个授权码，无孤儿绑定与失效码",
        )
