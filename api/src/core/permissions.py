"""权限点目录。

核心思想：**权限点由拥有它的模块自己声明**，核心只负责汇总和判定。

模块只要在 `ModuleSpec(permissions=[...])` 里列出自己的权限码，
自动发现时会收进全局目录；`require("task:create")` 即可保护路由，
新增权限点不需要修改 `api/deps.py` 或任何核心文件。

权限码约定：`资源:动作`，如 `task:create`、`asset:delete`、`billing:read`。
支持两种通配：`task:*`（某资源全部动作）、`*`（超级权限）。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.registry import RegistryError


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
