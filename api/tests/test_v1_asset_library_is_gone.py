"""v1 素材库（`assets` 表 / `modules/asset`）已彻底下线，不许复活。

2026-11-13 的决定：删表，不降级成只读索引。理由是「两套 id 空间并存」的代价
落在每一个下游——每个读 `asset_id` 的地方都得先判断「这个 id 属于谁」，而那正是
这次要根除的东西。历史任务的参考图展示因此会失效，这是明确接受的代价。

这条守卫钉的是**下线之后不许出现的东西**。五条判据对应五种回归方式：

1. **import 级**：有人从 git 历史里把 `modules/asset` 或注入口捞回来。
   `ruff` / `mypy` 不抓（它们只检查被点名的文件），CI 也不会因为
   「多了一个没人 import 的模块」变红——只有这条会。
2. **符号级**：同名函数被就地重写一遍（不是 import，而是 `def get_asset_lookup`）。
3. **模型级**：`assets` 表被重新声明进 `Base.metadata`。那样 `create_all`
   会在空库上重新建表，而迁移链里的 `drop_table` 只在**升级路径**上生效，
   两边从此不再描述同一个库。
4. **SQL 级**：手写 SQL 里出现 `FROM assets` / `JOIN assets`。这类查询在
   测试库上可能侥幸成立，线上直接 500。
5. **路由级**：`/assets` 前缀的接口被重新挂上。前端旧页面会因此「看起来能用」，
   读的却是已经删掉的语义（`category` / `shared` / `deletedAt`）。

**判据只看代码，不看注释与 docstring**：历史说明里出现 `StoredMedia` 这类名字
是有意保留的上下文（「为什么不再用它」），把它算成违规会逼人删掉注释。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

#: 已经删除、不许再 import 的模块路径前缀。
_DEAD_MODULES = ("modules.asset", "modules.template.id_space", "core.media")

#: 已随 v1 删除的符号。按名字扫，不只看 import —— 就地重写一遍同样是复活。
_DEAD_SYMBOLS = frozenset(
    {
        "get_asset_lookup",
        "register_asset_lookup",
        "reset_asset_lookup",
        "has_asset_lookup",
        "bind_asset_reference_sources",
        "reset_asset_reference_checker",
        "ask_asset_references",
        "reference_source_gaps",
        "ASSET_ID_SPACE_V1",
        "ASSET_ID_SPACE_V2",
        "CURRENT_ASSET_ID_SPACE",
        "asset_id_space_default",
        "AssetLookup",
        "AssetReferenceChecker",
        "AssetReferenceSource",
        "StoredMedia",
    }
)

#: 裸 SQL 里的 v1 表名。`library_assets` 是 v2，`FROM assets` 与它不匹配
#: （`_` 是词字符，`\bassets` 不会在那里面命中）。
_DEAD_TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+assets\b", re.IGNORECASE)


def _docstring_lines(tree: ast.Module) -> set[int]:
    """docstring 覆盖的行号。

    裸 SQL 那条判据要跳过它们：「以前这里是一条 `SELECT id FROM assets ...`」
    是说清来由的唯一线索，删掉注释比留着一句死 SQL 更糟。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if not (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            continue
        first = body[0].lineno
        lines.update(range(first, (body[0].end_lineno or first) + 1))
    return lines


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _code_nodes(tree: ast.Module):
    """遍历代码节点，跳过 docstring。

    直接 `ast.walk` 会把 docstring 也扫一遍，而「为什么不再用 `StoredMedia`」
    这类说明正是要保留的东西。
    """
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                skip.add(id(body[0].value))
    for node in ast.walk(tree):
        if id(node) not in skip:
            yield node


def test_no_source_file_imports_the_deleted_modules() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                for dead in _DEAD_MODULES:
                    if name == dead or name.startswith(dead + "."):
                        offenders.append(
                            f"{path.relative_to(SRC).as_posix()} → {name}"
                        )
    assert not offenders, (
        "v1 素材库已于 2026-11-13 下线，这些 import 必须删掉：\n"
        + "\n".join(f"  - {item}" for item in offenders)
    )


def test_no_source_file_uses_the_deleted_symbols() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(SRC).as_posix()
        for node in _code_nodes(tree):
            hit = None
            if isinstance(node, ast.Name) and node.id in _DEAD_SYMBOLS:
                hit = node.id
            elif isinstance(node, ast.Attribute) and node.attr in _DEAD_SYMBOLS:
                hit = "." + node.attr
            elif isinstance(node, ast.arg) and node.arg in _DEAD_SYMBOLS:
                hit = node.arg
            # `def get_asset_lookup(...)` 里的名字不是 `Name` 节点：只扫 Name
            # 会漏掉「就地重写一遍」这条最容易发生的复活路径。
            elif (
                isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                and node.name in _DEAD_SYMBOLS
            ):
                hit = "def " + node.name
            if hit is not None:
                offenders.append(f"{relative}:{node.lineno} → {hit}")
    assert not offenders, (
        "这些符号随 v1 素材库一起删除了，代码里不许再用"
        "（就地重写一遍同样是复活）：\n"
        + "\n".join(f"  - {item}" for item in offenders)
    )


def test_assets_table_is_not_declared_in_metadata() -> None:
    """`assets` 不许回到 ORM 元数据里。"""
    from api.discovery import active_modules
    from db.base import Base

    active_modules()  # 副作用：import 所有模块，把表登记进 metadata
    tables = set(Base.metadata.tables)
    assert "assets" not in tables, (
        "`assets` 表被重新声明了。v1 已于 2026-11-13 下线；"
        "要在资产库里存东西请用 `library_assets`（v2）。"
    )
    assert "library_assets" in tables, "对照项缺失：`library_assets` 才是现役资产表"


def test_no_raw_sql_touches_the_deleted_table() -> None:
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        skip = _docstring_lines(ast.parse(text))
        for lineno, line in enumerate(text.splitlines(), 1):
            if lineno in skip or line.lstrip().startswith("#"):
                continue
            if _DEAD_TABLE.search(line):
                offenders.append(f"{path.relative_to(SRC).as_posix()}:{lineno}")
    assert not offenders, (
        "手写 SQL 还在读已删除的 `assets` 表：\n"
        + "\n".join(f"  - {item}" for item in offenders)
    )


def test_no_route_is_mounted_under_the_assets_prefix() -> None:
    """`/assets` 前缀的接口一个都不该有。"""
    from app import create_app

    paths = create_app().openapi()["paths"]
    offenders = sorted(path for path in paths if path.startswith("/assets"))
    assert not offenders, (
        "v1 `/assets` 接口已下线，不该再有：\n"
        + "\n".join(f"  - {item}" for item in offenders)
        + "\n资产库的现役前缀是 `/library/assets`。"
    )
