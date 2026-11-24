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
        elif isinstance(node, ast.Import):
            # `import a.b.c as x` 绑定的是 `x`；`import a.b.c` 绑定的是 `a`
            for alias in node.names:
                if alias.asname:
                    info.imports[alias.asname] = (alias.name, alias.name)
                else:
                    head = alias.name.split(".")[0]
                    info.imports[head] = (head, head)
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


def _resolve_module(
    name: str,
    dotted: str,
    modules: dict[str, _ModuleInfo],
    seen: frozenset[str] = frozenset(),
) -> str | None:
    """把一个名字解析成**模块**的 dotted 名（`mod.CONST` 里的 `mod`）。

    常量解析（`_resolve`）只认「名字本身就是一个字符串常量」，所以
    `from modules.library import access` 之后的 `access.PERM_DELETE_ANY`
    会被漏掉——`access` 不是常量，它是一个子模块。

    这里补上那一段：`access` → `modules.library.access`，拿到模块后再去它
    的顶层常量里找 `PERM_DELETE_ANY`。
    """
    key = f"{dotted}:{name}"
    if key in seen or dotted not in modules:
        return None
    seen = seen | {key}
    info = modules[dotted]
    if name in info.imports:
        target, original = info.imports[name]
        # 子模块优先：`from modules.library import access` → modules.library.access
        # （target 本身是包 `modules.library`，先试 child 才是对的）
        child = f"{target}.{original}"
        if child in modules:
            return child
        # `import a.b.c as x` → target 本身就是模块
        if target in modules:
            return target
        return _resolve_module(original, target, modules, seen)
    # 本模块内的子模块（相对导入 `from . import access`）
    child = f"{dotted}.{name}"
    if child in modules:
        return child
    return None


def _strings(node: ast.expr, dotted: str, modules: dict[str, _ModuleInfo]) -> Iterator[str]:
    """从一个表达式里榨出所有可能的权限码。

    三种形态：字符串常量 / 可解析的常量名 / 元组字面量，
    另加**跨模块属性**（`access.PERM_DELETE_ANY`）。
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node.value
    elif isinstance(node, ast.Name):
        resolved = _resolve(node.id, dotted, modules)
        if resolved is not None:
            yield resolved
    elif isinstance(node, ast.Attribute):
        # `access.PERM_DELETE_ANY`：`access` 是子模块，常量在它自己的顶层
        if isinstance(node.value, ast.Name):
            module = _resolve_module(node.value.id, dotted, modules)
            info = modules.get(module) if module else None
            if info is not None and node.attr in info.consts:
                yield info.consts[node.attr]
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
        apis = page_catalog.requires_of(page.code)
        # 只授接口、不授页面：这一页管辖的接口必须全部失效，否则这个页面码
        # 就没在管任何东西（没有任何接口的 pages 指向它 / 那些接口全被别的页覆盖）
        gated = page_catalog.gate(set(apis))
        dropped = [code for code in apis if code not in gated]
        if apis and not dropped:
            decorative.append(page.code)
    assert not decorative, (
        f"这些页面码关掉之后什么接口都没失效 -> {decorative}；"
        "检查有没有接口的 pages= 指向它，或者它的接口全被别的页面覆盖"
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
    # `model_driver:refresh` 已在 P4 运维页落地（page:ops），从豁免表移出。
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

    # 归属关系的权威就是接口侧的 `pages=`：不是从页面反推，直接读声明。
    declared_by_pages = {spec.code for spec in catalog.all() if spec.pages}

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

    now_covered = {spec.code for spec in catalog.all() if spec.pages}

    stale = sorted(
        code
        for code in BACKGROUND_ONLY_PERMISSIONS
        if code in now_covered
    )
    assert not stale, (
        f"这些权限码现在已经有页面声明了，请从豁免表删掉 -> {stale}"
    )


# --------------------------------------------------------------------------- #
# 服务层常量写法（`access.PERM_DELETE_ANY`）的覆盖保障
#
# 背景：守卫的 `_ENFORCEMENT_CALLS` 一直包含 `has`，所以 `actor.has(...)`
# 本身是被扫到的。真正的盲区在**参数解析**——`access.PERM_DELETE_ANY`
# 是 `ast.Attribute`（模块属性），而常量解析只认裸名字（`ast.Name`），
# 于是这 13 处调用点解析不出权限码。
#
# 后果不是「码没被看见」（这两个码在 `lifecycle.py` 里有字面量写法，
# 码一直都在扫描结果里），而是「**调用点**没被看见」：那十几处
# 改名、挪走或拼错时，没有任何测试会报错。
#
# 解法是让守卫能跟着属性链找到常量（`_resolve_module`），而不是把代码
# 降级成字面量——常量能防拼错，13 处判定点已经用上了这个好处。
# --------------------------------------------------------------------------- #
#: 这些码在服务层用**模块属性**引用，守卫必须在那些文件里看到判定点。
CROSS_MODULE_CONSTANT_CODES: dict[str, tuple[str, ...]] = {
    "library:delete:any": (
        "modules.library.bulk:",
        "modules.library.downloads:",
        "modules.library.service:",
        "modules.library.tags:",
        "modules.library.text_assets:",
    ),
    "library:draft:review": (
        "modules.library.service:",
        "modules.library.tags:",
    ),
}


def test_cross_module_constant_codes_reach_the_scan(
    enforcement: dict[str, set[str]],
) -> None:
    """用模块属性写出来的判定点，必须真的进了守卫的视野。

    钉的是**位置**而不是码：码早就在扫描结果里（别处有字面量写法），
    盲区在调用点。少一个位置就说明那处判定点退化成了「不受任何守卫保护」。
    """
    missing: list[str] = []
    for code, prefixes in CROSS_MODULE_CONSTANT_CODES.items():
        seen = enforcement.get(code, set())
        for prefix in prefixes:
            if not any(site.startswith(prefix) for site in seen):
                missing.append(f"{code} 在 {prefix}* 看不到判定点")
    assert not missing, (
        "这些服务层判定点没被扫描到（常量引用成了盲区）-> "
        + "; ".join(missing)
        + "；若该调用点已改写字面量，请同步更新本表"
    )


def test_enforcement_attribute_arguments_are_resolvable(
    modules: dict[str, _ModuleInfo],
) -> None:
    """判定点里凡是**静态常量引用**（`mod.CONST`），那个常量必须能解出来。

    这条把那个盲区钉死：将来有人写成 `has(config.PERM_X)`（`config` 是个
    导入的模块，但常量名写错或常量挪进了类里），这里就会报错——
    「解不出来」等于「这个判定点不受任何守卫保护」。

    **不拦运行时动态值**：`actor.has(source.permission)`（导出来源把权限码
    放在数据里）静态分析本来就读不出来，它由 `_PERMISSION_ATTRS` 那条
    路径覆盖，不是这里的事。
    """
    problems: list[str] = []
    for dotted in modules:
        path = _module_path(dotted)
        if not path.is_file():
            continue
        info = modules[dotted]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in _ENFORCEMENT_CALLS:
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Attribute):
                    continue
                if not isinstance(arg.value, ast.Name):
                    continue
                base = arg.value.id
                module = _resolve_module(base, dotted, modules)
                if module is None:
                    # base 不是模块：要么是运行时值（`source.permission`），
                    # 要么是个**导入名却解析不出模块**——后者是拼错，拦下
                    if base in info.imports:
                        problems.append(f"{dotted}:{node.lineno}: {base}.{arg.attr}")
                    continue
                target = modules.get(module)
                if target is None or arg.attr not in target.consts:
                    problems.append(f"{dotted}:{node.lineno}: {base}.{arg.attr}")
    assert not problems, (
        "这些判定点用了静态常量引用，但解析不出权限码 -> "
        + "; ".join(sorted(problems)[:10])
        + "；常量必须在目标模块的顶层"
    )
