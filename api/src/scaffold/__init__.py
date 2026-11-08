"""新模块脚手架。

目标：把「新增一个业务模块」从 30 分钟的复制粘贴，压缩成一条命令，
并且生成的代码**天然满足所有架构约束**（自动发现、权限声明、分层、依赖方向）。

用法：
    app new-module voice --label 配音 --perm voice --slot 3
    app new-module voice --generation     # 额外生成生成流水线骨架

`--slot` 不是装饰：它会被写进 `errors.py` 的 `ModuleErrors(slot=N)`。
约束的粒度是**码值**而不是号段：号段只有 9 个而模块会更多，所以号段按业务
领域共用（`template` / `storyboard` / `shot` 就共用 7 号）；但两个模块返回
同一个 `code`，前端就分不清是谁失败了，所以生成前会逐个比对已占用的码值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scaffold.templates import (
    DEPENDENCIES_TMPL,
    ERRORS_TMPL,
    GENERATION_TMPL,
    INIT_TMPL,
    MODELS_TMPL,
    ROUTER_TMPL,
    SCHEMA_TMPL,
    SERVICE_TMPL,
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# 从现有模块的 errors.py 里读出号段，用来把下标还原成完整码值
_SLOT_RE = re.compile(r"""ModuleErrors\(\s*["'][\w]+["']\s*,\s*slot\s*=\s*(\d)""")
# 只认 `code = errors.code(409, 1)` 这种**定义行**。docstring 里常写
# `errors.code(409, 1)   # -> 40931` 作说明，那些不能被算成占用。
_CODE_DEF_RE = re.compile(
    r"^\s*code\s*=\s*errors\.code\(\s*(\d{3})\s*,\s*(\d)\s*\)", re.MULTILINE
)


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
    # 生成能力。只在 `with_generation` 时有意义，但**不能有默认值**：
    # 模板里曾经写死 `Capability.VIDEO`，于是 `new-module image --generation`
    # 生成的是一个「kind=image、能力=video」的流水线——框架会把生图请求路由到
    # 视频供应商，而且静态守卫（pipeline 必须实现协议）照样全绿，因为协议对了。
    # 这种错不会在启动时报，只会在第一次真实提交时把图发给错的供应商。
    capability: str = ""

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
            # 流水线骨架里的能力枚举名（大写成员名），如 CAPABILITY -> IMAGE
            "capability": self.capability.upper(),
            # 流水线靠「副作用导入」注册；漏了这一行，dispatch 会报「未注册」。
            # 结尾的 `\n` 是给模板用的：占位符独占一行，有值时它替换掉那一行，
            # 无值时那一行就是空行——两种情况下导入块与 `module =` 之间都恰好
            # 一个空行（否则 `ruff check` 报 I001，而新人会去手改生成物）。
            "generation_import": (
                f"from modules.{self.name} import pipeline  "
                "# noqa: F401  副作用导入：把流水线注册到 generation.pipeline\n"
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
        raise ScaffoldError(
            f"必须显式指定 --slot 1-9（当前 {plan.slot}；0 为框架保留），"
            "取值见 docs/EXTENDING.md 第 7 节的号段分配表"
        )
    if plan.with_generation and not plan.capability:
        raise ScaffoldError(
            "带 --generation 时必须显式指定 --capability（image / video / audio）："
            "流水线的 capability 决定「这个模块的请求会被交给哪一类供应商」，"
            "猜错不会报错，只会把请求发给错的供应商"
        )
    if plan.capability and not plan.with_generation:
        raise ScaffoldError("--capability 只在 --generation 时有意义，去掉其中一个")


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
        # 错误码必须一开始就有：等到「要区分 409 和 404」时才补，
        # 模块里已经散落着框架级 BadRequest，重构成本比一开始生成高得多（R4-02）
        "errors.py": ERRORS_TMPL.substitute(ctx),
    }
    if plan.with_generation:
        files["pipeline.py"] = GENERATION_TMPL.substitute(ctx)
    return files


def _used_codes(root: Path) -> dict[int, str]:
    """扫描现有模块的 `errors.py`，返回 {错误码值: 模块名}。

    号段要配合 seq 才能还原成码值，所以先读 `slot=N` 再逐条算。
    读不到号段的文件直接跳过——那种 `errors.py` 不是本脚手架生成的。
    """
    used: dict[int, str] = {}
    modules_dir = root / "src" / "modules"
    if not modules_dir.is_dir():
        return used
    for errors_file in modules_dir.glob("*/errors.py"):
        text = errors_file.read_text(encoding="utf-8")
        slot = _SLOT_RE.search(text)
        if slot is None:
            continue
        base = int(slot.group(1)) * 10
        for status, seq in _CODE_DEF_RE.findall(text):
            used[int(status) * 100 + base + int(seq)] = errors_file.parent.name
    return used


def create_module(plan: ModulePlan, root: Path) -> list[Path]:
    """在 `<root>/src/modules/<name>/` 下生成文件。

    `root` 必须是**含 `src/modules` 的那一层**，不是仓库根。本仓库的 Python
    源码根是 `api/`，所以在仓库根执行时要传 `--root api`。
    """
    modules_dir = root / "src" / "modules"
    if not modules_dir.is_dir():
        # 指错 root 时不能默默造一棵孤儿树：文件语法正确、退出码 0，
        # 唯一症状是 `app modules` 里查无此模块，排查会先跑到 discovery 上。
        raise ScaffoldError(
            f"{root} 下没有 src/modules，看起来不是项目根。"
            "请在 api/ 目录下执行，或显式传 --root api"
        )

    target = modules_dir / plan.name
    if target.exists():
        raise ScaffoldError(f"目录已存在，拒绝覆盖: {target}")

    files = render(plan)

    # 号段可共用，码值不能。这里做的是**生成前**的预检：`ModuleErrors` 在
    # 导入期还有一道同样的闸门，但那时候错误要等到 `app serve` 才暴露。
    used = _used_codes(root)
    wanted = {
        int(status) * 100 + plan.slot * 10 + int(seq)
        for status, seq in _CODE_DEF_RE.findall(files["errors.py"])
    }
    clashes = {code: used[code] for code in wanted if code in used}
    if clashes:
        detail = "、".join(
            f"{code}（{owner}）" for code, owner in sorted(clashes.items())
        )
        raise ScaffoldError(
            f"错误码 {detail} 已被占用。号段（slot {plan.slot}）可以共用，"
            "同一个码值不能；请换一个 slot，或改掉新模块 errors.py 里的示例 seq"
        )

    target.mkdir(parents=True)
    written: list[Path] = []
    for filename, content in files.items():
        path = target / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
