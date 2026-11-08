"""权限点的**引用侧**守卫：声明了就必须有人真的拿它做判断。

## 为什么需要这组测试

已有的两条守卫只看一个方向：

- `test_architecture.py::test_required_codes_are_declared_somewhere`：
  路由 `require(...)` 用到的码**必须有声明**（防拼错 → 普通用户永远 403）；
- `test_architecture.py::test_export_sources_declare_known_permissions`：
  导出源的 `permission` **必须有声明**。

反方向没人管：**声明了但没有任何地方 `require` / `actor.has` 它**。后果不是报错，
而是「看起来有权限体系，实际那个开关什么也不控制」：

1. 角色配置界面会把它列成一个永远勾了也没用的选项，管理员据此以为配好了；
2. 它掩盖了真正的缺口——该加的 `actor.has("...")` 忘了加，症状和「权限点还没人用」
   完全一样，而权限清单越长，越没人敢删；
3. 权限点改名/删除时，僵尸点没人发现，最后所有人对它失去信任。

## 为什么用 AST 而不是运行时

运行时看不到「没被调用」的代码——声明表是静态的，判定点是散在服务层里的。

`actor.has(PERM_READ_ALL)` 这种**常量**写法很常见，所以扫描必须能跨文件解析
`from modules.x.permissions import PERM_READ_ALL`，否则会把一半正常代码报成僵尸
（本仓库 33 个权限点里有 12 个是这么被引用的，只扫字面量会得到 12 条假阳性）。

## 新写权限判定时的注意事项

判定点必须用 `require(...)` 或 `actor.has / has_all / has_any(...)`，
或者把码放进导出源的 `permission` 属性：这三种形状在本文件的 `_ENFORCEMENT_CALLS`
里登记过。新增第四种判定方式（比如 `actor.can(...)`）时，**要同步更新这里**，
否则那个码会被判成僵尸——这正是我们希望它报错的方式：宁可误报一次，也不要静默漏掉。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from api.discovery import discover_modules
from core.permissions import catalog

SRC = Path(__file__).resolve().parents[1] / "src"

#: 能作为「权限点被真的用于判定」的调用名
_ENFORCEMENT_CALLS = frozenset({"require", "has", "has_all", "has_any"})

#: 权限码可以挂在数据对象上的属性名（导出源就是这么声明的）
_PERMISSION_ATTRS = frozenset({"permission"})

#: 通配符：不指向具体权限点，不参与「是否被引用」的核对
_WILDCARDS = frozenset({"*"})


@dataclass
class _ModuleInfo:
    """一个模块文件里「名字 → 字符串」的可解析信息。"""

    consts: dict[str, str] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)


def _dotted_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_path(dotted: str) -> Path:
    base = SRC / dotted.replace(".", "/")
    module = base.with_suffix(".py")
    return module if module.is_file() else base / "__init__.py"


def _scan_module(path: Path) -> _ModuleInfo:
    """只认模块顶层的赋值与导入：函数里的局部变量同名不同义，收进来会算错。"""
    info = _ModuleInfo()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
            if (
                len(names) == 1
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                info.consts[names[0]] = value.value
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            if (
                isinstance(node.target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                info.consts[node.target.id] = value.value
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                info.imports[alias.asname or alias.name] = (node.module, alias.name)
    return info


@pytest.fixture(scope="module")
def modules() -> dict[str, _ModuleInfo]:
    return {
        _dotted_name(path): _scan_module(path)
        for path in sorted(SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _resolve(
    name: str,
    dotted: str,
    modules: dict[str, _ModuleInfo],
    seen: frozenset[str] = frozenset(),
) -> str | None:
    """把一个名字解析成字符串常量，跟着 `from X import Y` 跨文件走。

    访问过的是 `模块:名字` 而不是只有名字：同名的 `PERM_READ` 在 `task` 与
    `identity` 里指向不同权限码，只记名字会在第一层就把自己挡回去。
    """
    key = f"{dotted}:{name}"
    if key in seen or dotted not in modules:
        return None
    seen = seen | {key}
    info = modules[dotted]
    if name in info.consts:
        return info.consts[name]
    if name in info.imports:
        target, original = info.imports[name]
        return _resolve(original, target, modules, seen)
    return None


def _strings(node: ast.expr, dotted: str, modules: dict[str, _ModuleInfo]) -> Iterator[str]:
    """从一个表达式里榨出所有可能的权限码（字符串常量 / 可解析的常量名 / 元组字面量）。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node.value
    elif isinstance(node, ast.Name):
        resolved = _resolve(node.id, dotted, modules)
        if resolved is not None:
            yield resolved
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for element in node.elts:
            yield from _strings(element, dotted, modules)


def _enforcement_sites(modules: dict[str, _ModuleInfo]) -> dict[str, set[str]]:
    """扫出所有「拿权限码做判断」的位置，`码 -> {位置}`。"""
    sites: dict[str, set[str]] = {}
    for dotted in modules:
        path = _module_path(dotted)
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            exprs: list[ast.expr] = []
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name in _ENFORCEMENT_CALLS:
                    exprs.extend(node.args)
                exprs.extend(kw.value for kw in node.keywords if kw.arg in _PERMISSION_ATTRS)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                # `permission = "..."` 与 `permission: str | None = "..."` 两种写法
                raw_targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None and any(
                    isinstance(t, ast.Name) and t.id in _PERMISSION_ATTRS
                    for t in raw_targets
                ):
                    exprs.append(node.value)
            for expr in exprs:
                for code in _strings(expr, dotted, modules):
                    sites.setdefault(code, set()).add(f"{dotted}:{getattr(node, 'lineno', '?')}")
    return sites


@pytest.fixture(scope="module")
def enforcement(modules: dict[str, _ModuleInfo]) -> dict[str, set[str]]:
    return _enforcement_sites(modules)


def _declared() -> set[str]:
    declared = set(catalog.codes())
    for spec in discover_modules():
        declared |= set(spec.permission_codes())
    return declared


def test_scan_is_not_vacuous(enforcement: dict[str, set[str]]) -> None:
    """扫描本身要有产出，否则下面两条会变成「永远通过的空检查」。

    这是本文件唯一的元测试：AST 扫描被重构坏掉时（比如 `_ENFORCEMENT_CALLS`
    里漏了 `has`），僵尸清单会暴涨，但「引用侧没有非法码」那条依然全绿——
    那种「一半守卫静默失效」的状态必须由数字暴露出来。
    """
    declared = _declared()
    assert len(declared) >= 30, f"权限点声明只有 {len(declared)} 个，扫描口径可能坏了"
    assert len(enforcement) >= len(declared), (
        f"扫描只认出 {len(enforcement)} 个码，少于声明的 {len(declared)} 个；"
        "说明常量解析或调用名清单退化了（宁可这里红，也不要僵尸清单失真）"
    )


#: 允许「声明了但暂时没人用」的权限点。**当前为空，请保持为空。**
#:
#: 加条目等于承认「这个开关什么也不控制」。真需要预留权限点时，
#: 应该先把它从 `ModuleSpec.permissions` 里去掉，等真正用上再加回来——
#: 权限清单是给管理员看的配置面，里面不该有装饰品。
ALLOWED_UNENFORCED: dict[str, str] = {}


def test_every_declared_permission_is_enforced(enforcement: dict[str, set[str]]) -> None:
    """声明的每个权限码，都必须在某个执行位置被判断过。"""
    zombies = sorted(code for code in _declared() if code not in enforcement)
    zombies = [code for code in zombies if code not in ALLOWED_UNENFORCED]

    assert not zombies, (
        "这些权限点声明了但没有任何 `require(...)` / `actor.has(...)` 判定 -> "
        f"{zombies}\n"
        "要么补上判定点，要么从 `ModuleSpec.permissions` 里删掉。"
        "放着不管的结果是角色配置界面多出一个永远无效的勾选项。"
    )


def test_allowlist_has_no_stale_entries(enforcement: dict[str, set[str]]) -> None:
    """豁免表里的条目一旦真的被用上了，就该删掉。

    棘轮：留着过期豁免，下一个人会照着它判断「这里已经有人想过了」。
    """
    stale = sorted(code for code in ALLOWED_UNENFORCED if code in enforcement)
    assert not stale, f"这些权限点已经有判定点了，请从 ALLOWED_UNENFORCED 里删掉 -> {stale}"


def test_enforced_codes_are_all_declared(enforcement: dict[str, set[str]]) -> None:
    """服务层 `actor.has("...")` 里的码也必须有人声明。

    `test_architecture.py` 那条只扫 `require(...)` 字面量；服务层判断（数据范围
    开关、跨用户重试）走的是 `actor.has(...)`，拼错时症状一模一样：超管正常、
    别人永远 403，而路由层的检查看不见它。
    """
    declared = _declared()
    unknown = sorted(
        code
        for code in enforcement
        if code not in declared and code not in _WILDCARDS and not code.endswith(":*")
    )
    assert not unknown, (
        "这些权限码被用来做判断，但没有任何模块声明过（普通用户会永远 403）-> "
        + "\n".join(f"{code}  <- {sorted(enforcement[code])[:3]}" for code in unknown)
    )


def test_builtin_role_permissions_are_declared() -> None:
    """内置角色模板里的权限码必须真的存在。

    写错的症状：角色建出来、登录也正常，只是那个能力永远没有——超管自测时
    完全看不出来（`*` 直接放行）。`service.py` 里那段「补齐内置角色缺失权限点」
    的注释记录过一次真实事故（`template:use` 声明了但 editor 角色里漏了）。
    """
    from modules.identity.permissions import BUILTIN_ROLES

    declared = _declared()
    resources = {code.partition(":")[0] for code in declared}

    problems: list[str] = []
    for role in BUILTIN_ROLES:
        for code in role.permissions:
            if code in _WILDCARDS:
                continue
            if code.endswith(":*"):
                if code.partition(":")[0] not in resources:
                    problems.append(f"{role.code}: {code} 覆盖不到任何已声明的权限点")
                continue
            if code not in declared:
                problems.append(f"{role.code}: {code} 没有任何模块声明")
    assert not problems, "内置角色的权限码对不上权限目录 -> " + str(problems)
