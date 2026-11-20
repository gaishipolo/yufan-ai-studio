"""权限点目录。

核心思想：**权限点由拥有它的模块自己声明**，核心只负责汇总和判定。

模块只要在 `ModuleSpec(permissions=[...])` 里列出自己的权限码，
自动发现时会收进全局目录；`require("task:create")` 即可保护路由，
新增权限点不需要修改 `api/deps.py` 或任何核心文件。

权限码约定：`资源:动作`，如 `task:create`、`asset:delete`、`billing:read`。
支持两种通配：`task:*`（某资源全部动作）、`*`（超级权限）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.context import SUPER_PERMISSION
from core.logging import get_logger
from core.registry import RegistryError

logger = get_logger(__name__)

#: 页面权限码的资源名。这里写字面量而不是 import `core.pages.PAGE_PREFIX`：
#: `core.pages` 反过来要用本模块的 `PermissionSpec`，import 会成环。
_PAGE_RESOURCE = "page"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """一个权限点的元信息，同时用于鉴权判定和「角色配置界面」的数据源。"""

    code: str
    name: str
    group: str = "通用"
    description: str = ""

    def __post_init__(self) -> None:
        if ":" not in self.code:
            raise ValueError(f"权限码必须形如 '资源:动作'，收到 {self.code!r}")


class PermissionCatalog:
    """全量权限点目录。启动时由模块自动发现填充。"""

    def __init__(self) -> None:
        self._specs: dict[str, PermissionSpec] = {}

    def declare(self, *specs: PermissionSpec) -> None:
        for spec in specs:
            existing = self._specs.get(spec.code)
            if existing is not None:
                # 两个模块声明同一个权限码 = 职责边界不清，直接暴露
                if existing != spec:
                    raise RegistryError(
                        f"权限码冲突 {spec.code!r}: "
                        f"{existing.name} vs {spec.name}，请确认归属模块"
                    )
                continue
            self._specs[spec.code] = spec

    def all(self) -> tuple[PermissionSpec, ...]:
        return tuple(sorted(self._specs.values(), key=lambda s: (s.group, s.code)))

    def codes(self) -> frozenset[str]:
        return frozenset(self._specs)

    def grouped(self) -> dict[str, list[PermissionSpec]]:
        """按分组返回，供「角色编辑」接口直接吐出。"""
        result: dict[str, list[PermissionSpec]] = {}
        for spec in self.all():
            result.setdefault(spec.group, []).append(spec)
        return result

    def __len__(self) -> int:
        return len(self._specs)

    def clear(self) -> None:
        """仅测试使用。"""
        self._specs.clear()


catalog = PermissionCatalog()


def expand_grants(codes: Iterable[str]) -> set[str]:
    """把 `资源:*` 摊成本项目录里的具体码。

    ## 为什么必须摊开

    通配是「这个资源全部动作」的简写，而**页面门控是按具体码反查页面的**
    （`PageCatalog.pages_for("task:read")`）。`task:*` 反查不到任何页面，
    于是门控把它当成「没有页面声明的基础设施码」原样放行 —— 结果就是
    「关掉任务中心，持有 `task:*` 的人照样能调 `/tasks`」（2026-11-20 实测）。

    摊开之后 `require("task:read")` 判的是摊出来的具体码：页面没授予，
    它就不在集合里，403 如期出现。

    ## 两种通配不摊

    - `*`：超管旁路，调用方（`resolve_identity`）在门控之前就短路了，
      摊开也没有意义；
    - `page:*`：页面码的判定本来就是 `permission_holds`（它会认 `page:*`），
      摊成 9 个页面码只会让 `/me` 的出参变长，语义不变。

    ## 查不到成员的资源

    目录里没有这个资源的任何具体码（拼错、或模块已下线），**什么都不留** ——
    留着它等于让一个谁也解释不清的码继续生效。留一条 warning，因为静默丢弃
    比报错更坏：这条通配来自某次人的输入。
    """
    by_resource: dict[str, set[str]] = {}
    for code in catalog.codes():
        resource, _, _ = code.partition(":")
        by_resource.setdefault(resource, set()).add(code)

    expanded: set[str] = set()
    unresolved: list[str] = []
    for code in codes:
        if code == SUPER_PERMISSION or not code.endswith(":*"):
            expanded.add(code)
            continue
        resource = code[: -len(":*")]
        if resource == _PAGE_RESOURCE:
            expanded.add(code)
            continue
        members = by_resource.get(resource)
        if members:
            expanded |= members
        else:
            unresolved.append(code)

    if unresolved:
        logger.warning(
            "通配码在目录里没有对应的具体权限点，已忽略：%s", sorted(set(unresolved))
        )
    return expanded
