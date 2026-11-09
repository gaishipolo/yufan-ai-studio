"""主体可达性：发布前必须过的两条断言。

## 为什么是纯函数

这两条判定决定"这个主体能不能被调用"。写成服务方法就只能靠集成测试去撞，
而集成测试要造库、造用户、造素材——于是只会覆盖到"正常的那个路径"。
写成纯函数可以穷举：缺一个角色、缺一种能力、成员被软删、参考图不进输出，
各一条用例，十行一个。

## 两条断言的形状

| 断言 | 形状 | 不满足时 |
| --- | --- | --- |
| 角色可达 | `∀ role ∈ required_roles, ∃ m: m.role == role ∧ 未删除` | 模板按角色取图会取空 |
| 能力可达 | `requires ⊆ 交集{m.grants | m 参与输出}` | 声称能下载，实际某张图不给 |

## 为什么能力取交集

取并集会等于「公开一个产品 = 公开它全部参考原片」：只要有一个成员允许下载，
整个主体就可下载。交集（木桶最短边）是唯一不会放大的算法。

**注意粒度是分开的**：`generate` 要全体成员授予（生成要读全部字节），
`download` / `derive` 逐成员判（某张参考图不能下载，不影响别人下载主图）。
所以这里算的是"主体级能力"，逐成员的判定由调用方按 `grants` 自己做——
把两件事合成一个布尔会让"谁不能下载"这个问题失去答案。

## 硬约束：不允许出现枚举大小比较

代码里不许写 `member.visibility >= subject.visibility` 这类比较（设计 §6.1）。
能力一律走集合包含判定。可见域只回答"能不能发现"，不回答"能做什么"。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# 能力词表已下移到 `core/asset_capabilities.py`：模板的槽位声明（M3.5）也要用它，
# 而两个业务模块之间不许横向 import。这里 re-export 保持既有调用方不变——
# 「搬走定义」与「改所有调用点」是两件事，只有需要时才做第二件。
from core.asset_capabilities import CAPABILITIES, CAPABILITY_LABELS

__all__ = [
    "CAPABILITIES",
    "CAPABILITY_LABELS",
    "MemberView",
    "Reachability",
    "check_reachability",
    "effective_capabilities",
]


@dataclass(frozen=True, slots=True)
class MemberView:
    """判定需要的成员视图。**只带判定用到的字段**。

    传整个 ORM 行进来会让这个函数没法单测（要造 session），而它恰恰是最该被
    穷举的那个。字段少也意味着以后加列不会意外影响判定。
    """

    asset_id: str
    role: str = ""
    grants: Sequence[str] = ()
    in_output: bool = True
    #: 素材是否已被软删/回收站。软删的成员等于不存在，但**记录还在**，
    #: 所以不删行、只在这里把它判成不可用（历史版本仍要能读）。
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class Reachability:
    """一次判定的结果。`ok` 为真时两个缺失清单都是空的。"""

    missing_roles: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    #: 被软删但仍挂在主体上的成员。它不是"错误"，而是"待补图"：
    #: 主体进只读态，历史版本仍可读，新调用被拦。
    broken_members: tuple[str, ...] = ()
    #: 逐成员不可用的能力：`{asset_id: [cap, ...]}`。主体级取交集是为了拦发布，
    #: 这个映射是为了回答"到底谁不给"——没有它，用户只能一张张点开看。
    member_gaps: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing_roles and not self.missing_capabilities

    def why(self) -> str:
        """给用户看的一句话。发布按钮旁边显示的就是它。"""
        if self.ok:
            return ""
        parts: list[str] = []
        if self.missing_roles:
            parts.append("缺少角色：" + "、".join(self.missing_roles))
        if self.missing_capabilities:
            parts.append("缺少能力：" + "、".join(self.missing_capabilities))
        return "；".join(parts)


def effective_capabilities(members: Iterable[MemberView]) -> set[str]:
    """主体级能力 = 参与输出且未删除的成员的 `grants` 交集。

    **不含不进输出的成员**：它们是"只给模型看"的参考图，不构成对外承诺。
    把它们算进来会让"多挂一张仅参考的图"意外降级整个主体的能力——
    而用户完全看不出是这个原因。

    没有可用成员时返回空集（不是全集）：空主体不能对外承诺任何能力。
    """
    usable = [set(m.grants) for m in members if m.in_output and not m.deleted]
    if not usable:
        return set()
    return set.intersection(*usable)


def check_reachability(
    members: Sequence[MemberView],
    *,
    requires: Sequence[str],
    required_roles: Sequence[str],
) -> Reachability:
    """发布前置校验。纯函数，无 IO。

    `requires` / `required_roles` 为空表示"不要求"，此时对应断言恒真——
    这与"随便填个空就通过"是两件事：空要求是显式选择，而填错是配置错误。
    """
    broken = tuple(m.asset_id for m in members if m.deleted)

    present_roles = {m.role for m in members if not m.deleted and m.role}
    missing_roles = tuple(role for role in required_roles if role not in present_roles)

    caps = effective_capabilities(members)
    missing_caps = tuple(cap for cap in requires if cap not in caps)

    # 逐成员缺什么：只对参与输出且未删除的成员算，因为只有它们影响主体能力
    gaps: dict[str, tuple[str, ...]] = {}
    for member in members:
        if member.deleted or not member.in_output:
            continue
        lack = tuple(cap for cap in requires if cap not in set(member.grants))
        if lack:
            gaps[member.asset_id] = lack

    return Reachability(
        missing_roles=missing_roles,
        missing_capabilities=missing_caps,
        broken_members=broken,
        member_gaps=gaps,
    )
