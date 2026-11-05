"""新模块脚手架。

目标：把「新增一个业务模块」从 30 分钟的复制粘贴，压缩成一条命令，
并且生成的代码**天然满足所有架构约束**（自动发现、权限声明、分层、依赖方向）。

用法：
    app new-module voice --label 配音 --perm voice --slot 3
    app new-module voice --generation     # 额外生成生成流水线骨架
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scaffold.templates import (
    DEPENDENCIES_TMPL,
    GENERATION_TMPL,
    INIT_TMPL,
    MODELS_TMPL,
    ROUTER_TMPL,
    SCHEMA_TMPL,
    SERVICE_TMPL,
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ScaffoldError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModulePlan:
    name: str
    label: str
    perm: str
    slot: int
    group: str = "业务"
    with_generation: bool = False

    @property
    def pascal(self) -> str:
        return "".join(part.capitalize() for part in self.name.split("_"))

    @property
    def table(self) -> str:
        return f"{self.name}s"

    @property
    def context(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "Name": self.pascal,
            "label": self.label,
            "perm": self.perm,
            "slot": self.slot,
            "group": self.group,
            "table": self.table,
            # 流水线靠「副作用导入」注册；漏了这一行，dispatch 会报「未注册」
            "generation_import": (
                f"from modules.{self.name} import pipeline  "
                "# noqa: F401  副作用导入：把流水线注册到 generation.pipeline"
                if self.with_generation
                else ""
            ),
        }


def validate(plan: ModulePlan) -> None:
    if not _NAME_RE.match(plan.name):
        raise ScaffoldError(f"模块名必须是 snake_case：{plan.name!r}")
    if not plan.label.strip():
        raise ScaffoldError("必须提供模块中文名（--label）")
    if not _NAME_RE.match(plan.perm):
        raise ScaffoldError(f"权限前缀必须是 snake_case：{plan.perm!r}")
    if not 1 <= plan.slot <= 9:
        raise ScaffoldError("错误码 slot 必须为 1-9（0 为框架保留），见 docs/EXTENDING.md 分配表")


def render(plan: ModulePlan) -> dict[str, str]:
    """返回 {文件名: 内容}。"""
    validate(plan)
    ctx = plan.context

    files: dict[str, str] = {
        "__init__.py": INIT_TMPL.substitute(ctx),
        "router.py": ROUTER_TMPL.substitute(ctx),
        "schema.py": SCHEMA_TMPL.substitute(ctx),
        "service.py": SERVICE_TMPL.substitute(ctx),
        "dependencies.py": DEPENDENCIES_TMPL.substitute(ctx),
        "models.py": MODELS_TMPL.substitute(ctx),
    }
    if plan.with_generation:
        files["pipeline.py"] = GENERATION_TMPL.substitute(ctx)
    return files


def create_module(plan: ModulePlan, root: Path) -> list[Path]:
    """在 `<root>/src/modules/<name>/` 下生成文件。"""
    target = root / "src" / "modules" / plan.name
    if target.exists():
        raise ScaffoldError(f"目录已存在，拒绝覆盖: {target}")

    target.mkdir(parents=True)
    written: list[Path] = []
    for filename, content in render(plan).items():
        path = target / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
