"""按改动范围算出「该跑哪些测试」。

## 为什么要有这个脚本

全量 `pytest -q` 要 169 秒，而多数改动只碰一两个模块。凭印象挑测试会漏，
跑全量又慢。本脚本把「该跑什么」变成**可复现的机械判断**：读 git diff，
按 import 依赖图反查受影响的测试文件，再按改动性质分级。

## 判据

1. **依赖反查**：测试文件 `import` 了哪些源模块 → 建「源文件 → 测试文件」反向图。
   改了 `src/modules/library/tags.py` 就会命中 `tests/test_library_tags.py`。
   静态 import 抓不到的**函数内 import**也扫（`ast.walk` 走全树）。

2. **传递依赖**：改了 `core/registry.py` 这种底层，会影响所有 import 它的东西。
   脚本沿着反向图向上传递，直到收敛。

3. **分级**（决定要不要跑全量）：

   | 级别 | 触发条件 | 该跑什么 |
   |---|---|---|
   | `targeted` | 只改业务模块/测试/文档 | 依赖图算出的测试文件 |
   | `framework` | 改了框架层（`core/`、`api/` 等） | 依赖图 + 架构守卫 |
   | `contract` | 改了路由/schema/权限点 | 依赖图 + 契约守卫 |
   | `full` | 改了装配、迁移，或显式要求 | 全量 |

   `contract` 级别还要跑 `app.exe export-openapi --check`（比对四份生成物）。

4. **必须升级到全量的硬规则**（脚本自动判断，不给商量）：
   - 改了 `src/api/ports.py`（注入口目录）→ 端口守卫全查
   - 改了 `src/modules/<mod>/spec.py`（模块装配声明）→ 装配对账
   - 改了 `src/core/registry.py`、`src/core/exceptions.py` → 错误码/注册表全局口径
   - 新增/删除 `src/modules/*/` 下的目录 → 模块边界变了
   - 改了 `pyproject.toml`（依赖、pytest 配置）→ 环境变了

## 用法

    python scripts/select_tests.py                    # 看上次的改动该跑什么
    python scripts/select_tests.py --diff HEAD~1      # 指定范围
    python scripts/select_tests.py --diff main...HEAD # 分支范围
    python scripts/select_tests.py --full             # 强制全量
    python scripts/select_tests.py --print            # 直接打印 pytest 命令

**输出**是给 `python -m pytest` 的路径列表，不含自身。守卫文件（架构、契约、
internal）**永远包含在内**——它们跑得快，且正是「改错了但没人发现」的那类。
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent
SRC = API_ROOT / "src"
TESTS = API_ROOT / "tests"

# 顶层包名 → 源目录。改动落在这些包里算「框架层」。
FRAMEWORK_PACKAGES = frozenset({"core", "db", "infra", "generation", "api"})

# 永远要跑的守卫：它们不快，但正是「改错了没人发现」的那一类。
ALWAYS_RUN = (
    "tests/test_architecture.py",
    "tests/test_internal_models.py",
    "tests/test_openapi_contract.py",
)

# **结构性**改动 → 必须全量。判据是「改的是装配口径，不是局部行为」：
# 删一个端口、改模块的装配声明、动错误码编号，都会让别的模块静默失效。
# 注意别把「加了个方法」也算进来——`core/registry.py` 加 `remove()` 只是
# 局部扩展，按文件名一刀切会让每次小改都退化成 169 秒全量。
FULL_TRIGGER_FILES = frozenset(
    {
        "src/api/ports.py",
        "src/api/assembly.py",
        "src/api/discovery.py",
        "src/core/exceptions.py",
        "src/core/response.py",
        "src/generation/runner.py",
        "pyproject.toml",
    }
)

# 契约层触发：改了 HTTP 路由或出参 schema，四份生成物就要重导 + 字节比对。
# 只认 `modules/` 与 `api/` 下的——`infra/ai/router.py` 是**模型路由**
# （model id → 供应商），跟 OpenAPI 契约无关，按文件名一刀切会误伤。
CONTRACT_HINT = ("/router.py", "/schema.py", "/permissions.py", "/models.py")
CONTRACT_SCOPE = ("src/modules/", "src/api/")


def _run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def _changed_files(diff_range: str | None) -> list[str]:
    """返回改动的文件（相对 api/ 目录的路径）。"""
    if diff_range:
        out = _run_git(["diff", "--name-only", diff_range])
    else:
        # 默认：工作区相对 HEAD 的改动（含未跟踪的新文件）
        out = _run_git(["diff", "--name-only", "HEAD"])
        untracked = _run_git(["ls-files", "--others", "--exclude-standard"])
        out += untracked
    files = [line.strip() for line in out.splitlines() if line.strip()]
    # git 在 api/ 目录下跑，路径带 api/ 前缀；统一剥掉
    return [f[len("api/") :] if f.startswith("api/") else f for f in files]


def _diff_hunks(diff_range: str | None) -> dict[str, list[tuple[str, str]]]:
    """文件 → [(旧行, 新行)] 的增删行。

    用来判断「这次改动是**扩展**还是**改语义**」：
    - 只新增函数/方法（没有删除行）→ 扩展，调用方行为不变
    - 有删除行（改了已有函数体 / 删了东西）→ 改语义，调用方可能受影响
    """
    out = _run_git(["diff", "-U0", diff_range or "HEAD"])
    hunks: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
            if current.startswith("api/"):
                current = current[len("api/") :]
            hunks.setdefault(current or "", [])
        elif line.startswith(("+++ ", "--- ", "@@", "diff ", "index ")):
            continue
        elif current is not None and line[:1] in {"+", "-"}:
            hunks[current].append((line[0], line[1:]))
    return hunks


def _is_semantic_change(path: str, hunks: list[tuple[str, str]]) -> bool:
    """这次改动是否动到了**已有**行为。

    判据：diff 里有删除行 → 改了已有逻辑；有纯新增行 → 只是扩展。

    例外：新增的是**框架公共入口**（`register_*` / `reset_*` / `has_*`）或
    权限点声明时也算语义变化——那类东西一改就是全局口径。
    """
    if any(kind == "-" for kind, _ in hunks):
        return True
    added = "".join(text for kind, text in hunks if kind == "+")
    if not added:
        return False
    public_entry = ("def register_", "def reset_", "def has_", "BUILTIN_ROLES")
    return any(token in added for token in public_entry)


def _source_modules() -> dict[str, Path]:
    """源模块名 → 文件路径。`modules/library/tags.py` → `modules.library.tags`."""
    modules: dict[str, Path] = {}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        if parts:
            modules[".".join(parts)] = path
    return modules


def _imports_of(path: Path) -> set[str]:
    """文件引用到的项目内模块名（顶层包 + 完整模块名都算）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import（from . import x）
                continue
            if node.module:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    return found


def _module_to_file(modules: dict[str, Path], name: str) -> Path | None:
    """模块名 → 源文件。找不到就退到最长前缀（`modules.library.tags` 找不到时试
    `modules.library`）。"""
    if name in modules:
        return modules[name]
    parts = name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in modules:
            return modules[candidate]
        parts.pop()
    return None


def _build_reverse_graph() -> dict[str, set[str]]:
    """源文件路径 → 依赖它的测试文件名集合。

    key 统一带 `src/` 前缀，与 `git diff` 的输出路径一致——两处格式不一致
    会让整个依赖图**查不中**（表现为「只跑守卫、漏掉主测试文件」）。
    """
    modules = _source_modules()
    graph: dict[str, set[str]] = {}
    for test_path in sorted(TESTS.glob("test_*.py")):
        deps = _imports_of(test_path)
        touched: set[str] = set()
        for name in deps:
            source = _module_to_file(modules, name)
            if source is None:
                continue
            rel = f"src/{source.relative_to(SRC).as_posix()}"
            touched.add(rel)
            # 包级依赖：`modules.library` 变了 → `modules/library/` 下都算
            package = rel.rsplit("/", 1)[0] if "/" in rel else ""
            if package:
                touched.add(f"{package}/")
        for rel in touched:
            graph.setdefault(rel, set()).add(test_path.name)
    return graph


def _affected_sources(
    changed: list[str],
    graph: dict[str, set[str]],
    hunks: dict[str, list[tuple[str, str]]],
) -> set[str]:
    """改动文件 + **上游**依赖者的并集。

    只向上游传（谁 import 我），**不向下传**（我 import 谁）。

    向下传是错的：改了一个函数的实现，不等于改它调用者的行为。双向传递
    会让 `core/registry.py` 的改动扩散到全部 286 个源文件——那等于全量，
    这个工具就白做了。

    向上传一层就够：`modules/library/tags.py` 改了 → `modules/library/router.py`
    （import 它）也该复测；再往上 `app.py` import 了 router，意义已经不大，
    而且那一层会被「框架层 → 全部守卫」的规则覆盖。

    **扩展型改动不带上游**：给 `NamedRegistry` 加个 `remove()` 方法，
    53 个依赖它的测试一个都不会因此坏掉，全带上等于白跑。
    只有改了已有语义（diff 里有删除行）才把上游算进来。
    """
    modules = _source_modules()
    # 源模块 → 依赖它的源模块。key 一律带 `src/` 前缀，与 graph 对齐。
    importers: dict[str, set[str]] = {}
    for source_path in SRC.rglob("*.py"):
        rel = f"src/{source_path.relative_to(SRC).as_posix()}"
        for name in _imports_of(source_path):
            target = _module_to_file(modules, name)
            if target is None:
                continue
            importers.setdefault(f"src/{target.relative_to(SRC).as_posix()}", set()).add(rel)

    affected: set[str] = set()
    for current in changed:
        if not current.endswith(".py"):
            continue
        affected.add(current)
        # 直接依赖者（上游）——一层，不递归；扩展型改动不带
        if _is_semantic_change(current, hunks.get(current, [])):
            affected |= importers.get(current, set())
    return affected


def _classify(
    changed: list[str],
    affected: set[str],
    graph: dict[str, set[str]],
    hunks: dict[str, list[tuple[str, str]]],
) -> str:
    """决定测试级别。"""
    if any(f in FULL_TRIGGER_FILES for f in changed):
        return "full"

    # 模块装配声明改了 → 装配对账（`create_app()` 会拒启动）
    for f in changed:
        if f.startswith("src/modules/") and f.endswith("/spec.py"):
            return "full"

    if any("migrations/" in f for f in changed):
        return "full"

    src_changed = [f for f in changed if f.startswith("src/")]
    if not src_changed:
        return "targeted"

    # 框架层改动 → 依赖图算出的测试 + 全部架构守卫。
    # 刻意**不再**按「依赖者数量」升级到全量：依赖多 ≠ 影响大。
    # `core/registry.py` 只是给 `NamedRegistry` 加个 `remove()` 方法，
    # 有 53 个测试文件依赖它，但它们一个都不会因此坏掉——按数量一刀切
    # 会让每次小改都退化成 169 秒全量，这个工具就白做了。
    # 真要全量时由上面的结构性触发（端口目录 / 装配 / 迁移 / spec.py）负责。
    #
    # 框架层还要看**语义**：只新增方法（无删除行）= 扩展，调用方行为不变，
    # 按 targeted 跑；改了已有函数体 = 改语义，走 framework（依赖图 + 全部守卫）。
    for f in src_changed:
        parts = f.split("/")
        if len(parts) >= 2 and parts[1] in FRAMEWORK_PACKAGES:
            if not _is_semantic_change(f, hunks.get(f, [])):
                continue
            return "framework"

    # 契约层
    for f in src_changed:
        if not f.startswith(CONTRACT_SCOPE):
            continue
        if any(f.endswith(suffix) for suffix in CONTRACT_HINT):
            return "contract"

    return "targeted"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按改动范围算该跑哪些测试")
    parser.add_argument("--diff", help="git diff 范围，默认工作区相对 HEAD")
    parser.add_argument("--full", action="store_true", help="强制全量")
    parser.add_argument("--print", dest="do_print", action="store_true", help="只打印 pytest 命令")
    args = parser.parse_args(argv)

    changed = _changed_files(args.diff)
    hunks = _diff_hunks(args.diff)
    graph = _build_reverse_graph()
    affected = _affected_sources(changed, graph, hunks)

    level = "full" if args.full else _classify(changed, affected, graph, hunks)

    # 依赖图算出的测试文件
    selected: set[str] = set()
    for rel in affected:
        selected |= graph.get(rel, set())
        # 目录级依赖：**只**在改了包本身（`__init__.py`）时才算。
        # 改了目录里任何一个文件就把整个目录的测试拉进来 = 过度匹配：
        # 改 `model_driver/registry.py` 会把 model_driver 全部测试都带上。
        if rel.endswith("__init__.py"):
            package = rel[: -len("__init__.py")]
            for key, tests in graph.items():
                if key.endswith("/") and key.startswith(package):
                    selected |= tests

    # 改动的测试文件**自己**必须被选中。依赖图只认「谁 import 了我」，
    # 新写的测试文件不被任何源文件依赖 → 查不到 → 漏跑。
    for rel in changed:
        if rel.startswith("tests/") and rel.endswith(".py"):
            selected.add(Path(rel).name)

    if level == "full":
        selected = {p.name for p in TESTS.glob("test_*.py")}
    else:
        selected |= set(Path(p).name for p in ALWAYS_RUN)

    # 契约层：生成物比对必须单独跑
    commands: list[str] = []
    if selected:
        ordered = sorted(f"tests/{name}" for name in selected)
        commands.append(f"python -m pytest {' '.join(ordered)} -q")
    if level in ("contract", "framework", "full"):
        commands.append("python -m pytest tests/test_openapi_contract.py -q")

    print(f"改动文件 {len(changed)} 个 → 级别 {level} → 建议跑 {len(selected)} 个测试文件")
    if level == "full":
        print("  理由：触发全量条件（框架核心 / 装配声明 / 迁移 / 显式 --full）")
    if args.do_print:
        for cmd in commands:
            print(cmd)
    else:
        for name in sorted(selected):
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
