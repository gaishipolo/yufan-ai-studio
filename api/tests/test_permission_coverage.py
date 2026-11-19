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


def _page_codes() -> set[str]:
    """页面权限码（`page:*`）。

    它们不在 `require(...)` 里出现，而是由 `PageCatalog.gate()` 在
    `resolve_identity()` 里**一次性**判定：门控按「接口码 → 声明它的页面」反查，
    逐个页面去找调用点既找不到也没有意义。所以「是否被使用」对页面码要换判据：
    见 `test_every_page_code_can_be_switched_off`（关掉这一页，它的接口真的会失效）。
    """
    from core.pages import page_catalog

    return set(page_catalog.codes())


def test_scan_is_not_vacuous(enforcement: dict[str, set[str]]) -> None:
    """扫描本身要有产出，否则下面两条会变成「永远通过的空检查」。

    这是本文件唯一的元测试：AST 扫描被重构坏掉时（比如 `_ENFORCEMENT_CALLS`
    里漏了 `has`），僵尸清单会暴涨，但「引用侧没有非法码」那条依然全绿——
    那种「一半守卫静默失效」的状态必须由数字暴露出来。
    """
    declared = _declared() - _page_codes()
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
    zombies = sorted(
        code
        for code in _declared() - _page_codes()
        if code not in enforcement
    )
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


def test_every_page_code_can_be_switched_off() -> None:
    """页面码必须真的能关掉东西，而不是一个装饰用的开关。

    这是 `test_every_declared_permission_is_enforced` 对页面码的对应物：
    判据是「把这个页面码从授予里去掉，它声明的接口权限真的会失效」。
    """
    from core.pages import page_catalog

    decorative: list[str] = []
    for page in page_catalog.all():
        # 只授接口、不授页面：这一页声明的接口必须全部失效，否则这个页面码
        # 就没在管任何东西（requires 写空 / 写成了不受门控的基础设施码）
        gated = page_catalog.gate(set(page.requires))
        dropped = [code for code in page.requires if code not in gated]
        if page.requires and not dropped:
            decorative.append(page.code)
    assert not decorative, (
        f"这些页面码关掉之后什么接口都没失效 -> {decorative}；"
        "检查 requires 是不是写空了，或者它声明的接口全被别的页面覆盖"
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


#: 允许「有判定点、但没有任何页面声明」的权限码 —— 即**纯后台能力**。
#:
#: 页面门控的规则是「接口权限只有在至少一个声明它的页面被授予时才有效」。
#: 于是每个接口码都必须能回答一个问题：「它归哪个页面？」
#:
#: 两种答不出来的情况：
#:
#: 1. **该答但没人答**（真缺口）：前端页面在用这个接口，模块却没声明它。
#:    后果是「关掉那一页 ⇒ 它的接口自动禁用」这条规则对它不生效 ——
#:    页面没了，接口还能打。这是守卫要抓的主要目标。
#: 2. **本来就没有前端页面**（如 CLI / 运维脚本用的接口）：这类码**应该**
#:    不受页面门控，列在这里就是明确承认这一点。
#:
#: 写法上分两类：`*_BY_DESIGN` 是「本来就没有前端页面」，
#: `*_MISSING_PAGE` 是「有前端页面但漏声明了」—— 后者是待修的缺口，
#: 不该长期留着，所以它们的条目必须写清该归哪个页面。
#:
#: 加条目的代价：将来有人给这个码配了页面，这张表就该删掉它
#: （`test_page_exemptions_have_no_stale_entries` 会抓）。
BACKGROUND_ONLY_PERMISSIONS: dict[str, str] = {
    # export 模块：异步 CSV 导出。前端没有任何入口（产物走后台 + 对象存储），
    # 所以没有页面可以声明它 —— 它本来就不该被页面开关影响。
    "export:read": "export 模块无前端页面（CLI/运维用）",
    "export:create": "export 模块无前端页面（CLI/运维用）",
    "export:delete": "export 模块无前端页面（CLI/运维用）",
    "export:read:audit": "export 模块无前端页面（CLI/运维用）",
    # model_driver 的「统一生成接口」：前端走的是 task/image 模块的提交接口，
    # 这些 HTTP 端点是给程序化调用方（CLI、外部集成）用的。
    "model_driver:create": "统一生成接口，前端走 task/image 提交",
    "model_driver:cancel": "统一生成接口，前端走 task/image 提交",
    "model_driver:read:all": "model_driver:read 的附加开关，随主码走",
    "model_driver:retry:any": "model_driver:read 的附加开关，随主码走",
    "model_driver:cancel:any": "model_driver:read 的附加开关，随主码走",
}


def test_every_enforced_code_belongs_to_a_page_or_is_background_only(
    enforcement: dict[str, set[str]],
) -> None:
    """每个被判定点用到的权限码，要么有页面声明它，要么显式列为后台能力。

    这是「禁用页面 ⇒ 该页接口自动禁用」这条规则的**完整性**守卫：

    - 漏声明的码（前端在用、但模块没说它归哪页）→ 关掉那一页时它仍然有效，
      规则对它不生效；
    - 没列入豁免表的码 → 没人回答过「它归哪个页面」，多半就是漏声明。

    豁免表里必须写清理由：只有「本来就没有前端页面」才该豁免。
    """
    from core.pages import page_catalog

    declared_by_pages: set[str] = set()
    for page in page_catalog.all():
        declared_by_pages.update(page.requires)

    orphans = sorted(
        code
        for code in enforcement
        if code not in declared_by_pages
        and code not in BACKGROUND_ONLY_PERMISSIONS
        and not code.endswith("*")
        and code != "*"
    )
    assert not orphans, (
        "这些权限码有判定点，却既没有页面声明、也没列入后台能力豁免表 -> "
        + "\n".join(f"{code}  <- {sorted(enforcement[code])[:3]}" for code in orphans)
        + "\n要么给使用它的页面补上 requires，要么明确它是后台能力（写进豁免表并说明理由）。"
    )


def test_page_exemptions_have_no_stale_entries() -> None:
    """豁免表里的码一旦被某个页面声明了，就该删掉。

    棘轮：留着过期豁免，下一个人会照着它判断「这里已经有人想过了」。
    """
    from core.pages import page_catalog

    now_covered: set[str] = set()
    for page in page_catalog.all():
        now_covered.update(page.requires)

    stale = sorted(
        code
        for code in BACKGROUND_ONLY_PERMISSIONS
        if code in now_covered
    )
    assert not stale, (
        f"这些权限码现在已经有页面声明了，请从豁免表删掉 -> {stale}"
    )
