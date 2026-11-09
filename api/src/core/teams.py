"""首批团队与团队管理员账号的稳定标识。

## 为什么在 `core/` 而不是某个业务模块里

`identity` 要按这张表建 6 个账号，`asset` 要按它建 `team` 与 `team_member`，
而业务模块之间不许互相 import（见 EXTENDING §5.1）。两边共用一份常量表，
是耦合面最小的做法：谁都不认识对方的模型，只认识几个字符串。

这里只放**标识**（id / 名称 / 账号），不放任何规则。团队能不能改名、
谁能管理成员、跨团队可见性——那些是 asset 模块的业务，写在它的服务里。

## id 是稳定标识，不要跟着名字改

`team_member.team_id`、`category.owner_key` 都按它落库。改名等于一次数据迁移，
而「把 tech 改成技术组」这类诉求应当改 `name`，不是改 `id`。

## 账号为什么是占位而不是真实人员

真实人员的身份信息（手机、企业邮箱）不该写进代码。首发用 6 个可登录的占位账号
把流程跑通，人员接手时只改 `team_members.user_id` 一行，不动代码、不重跑迁移。
占位账号与演示账号走同一套 seed，所以**只存在于开发/演示库**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "TEAMS",
    "TEAM_ACCOUNTS",
    "TEAM_ADMIN_COUNT",
    "TeamAccount",
    "TeamSpec",
    "team_ids",
]


@dataclass(frozen=True, slots=True)
class TeamSpec:
    """一个团队的标识。`kind` 是机器可读的短标识，`name` 是给人看的。"""

    id: str
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class TeamAccount:
    """一个团队管理员占位账号。

    `role` 是**团队内**的角色（`admin` / `member`），与系统角色（editor / viewer）
    是两件事：系统角色决定「能做什么动作」，团队角色决定「能管哪一层库」。
    """

    user_id: str
    username: str
    display_name: str
    password: str
    team_id: str


# 三个团队的顺序就是顶栏切换器的默认顺序：内容 → 运营 → 技术。
# 顺序写死在常量里而不是按 id 排序，因为 `t_content` / `t_ops` / `t_tech`
# 的字典序恰好是 content < ops < tech，改 id 就会连带改界面顺序。
TEAMS: Final[tuple[TeamSpec, ...]] = (
    TeamSpec("t_content", "内容团队", "content"),
    TeamSpec("t_ops", "运营团队", "ops"),
    TeamSpec("t_tech", "技术团队", "tech"),
)

# 每团队一主一备：单人即单点故障，而他休假时待审区会开始堆积；
# 「临时给谁开一下权限」是一次不可审计的例外，开过第一次就会有第二次。
# 口令按正式策略（>=10 位、非纯数字），否则演示账号自己过不了改密那一步。
TEAM_ACCOUNTS: Final[tuple[TeamAccount, ...]] = (
    TeamAccount(
        "u_tm_content_lead", "tm.content.lead", "内容主管理员", "tm@content2026", "t_content"
    ),
    TeamAccount(
        "u_tm_content_backup", "tm.content.backup", "内容备管理员", "tm@content2026", "t_content"
    ),
    TeamAccount("u_tm_ops_lead", "tm.ops.lead", "运营主管理员", "tm@ops2026", "t_ops"),
    TeamAccount("u_tm_ops_backup", "tm.ops.backup", "运营备管理员", "tm@ops2026", "t_ops"),
    TeamAccount("u_tm_tech_lead", "tm.tech.lead", "技术主管理员", "tm@tech2026", "t_tech"),
    TeamAccount("u_tm_tech_backup", "tm.tech.backup", "技术备管理员", "tm@tech2026", "t_tech"),
)

# 对账用：`app doctor` 与 seed 都按它判断「账号数是否与声明一致」。
TEAM_ADMIN_COUNT: Final[int] = len(TEAM_ACCOUNTS)


def team_ids() -> tuple[str, ...]:
    """全部团队 id。顺序与 `TEAMS` 一致，供 seed 与校验复用。"""
    return tuple(spec.id for spec in TEAMS)
