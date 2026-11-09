"""团队的 HTTP 契约。

只读：写操作（建团队、加减成员、任命管理员）走 M5 的团队管理界面。
这一版的接口只为顶栏库切换器服务，所以出参刻意很窄——`id / name / kind` 加
一个「我在里面是什么角色」。成员明细不在这里返回：能看团队不等于能看人名，
后者要 `asset:team:member:manage`（M5 上线）。
"""

from __future__ import annotations

from core.schema import CamelModel


class TeamOut(CamelModel):
    id: str
    name: str
    # content / ops / tech，前端按它选图标与默认分类树
    kind: str
    # 当前用户在该团队的角色：admin / member；超管看别人团队时为空
    role: str = ""
    member_count: int = 0


class TeamList(CamelModel):
    items: list[TeamOut]
    # 供前端决定「是否显示团队管理入口」，与 `role=admin` 不是一回事：
    # 平台管理员能管所有团队，但可能不属于其中任何一个。
    can_manage: bool = False
