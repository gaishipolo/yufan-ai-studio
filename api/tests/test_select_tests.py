"""`scripts/select_tests.py`：按改动范围算该跑哪些测试。

这个工具决定「跑什么测试」，它自己错了就会**静默漏跑**——比测试写错更危险，
因为漏跑的测试不会报警。所以它的判据必须被钉住。

钉住四件事：
1. **依赖反查**：改了某个源文件 → 依赖它的测试文件被选中；
2. **只向上游传一层**：不向下传（否则一次小改就退化成全量）；
3. **纯扩展 vs 改语义**：只新增方法不算改语义，不该带上全部上游；
4. **分级**：结构性改动（端口目录 / 装配 / 迁移）必须升级到全量。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "select_tests.py"


def _load() -> object:
    """把脚本当模块加载（它不在 `src/` 下，不在 import 路径里）。"""
    spec = importlib.util.spec_from_file_location("_select_tests", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_select_tests"] = module
    spec.loader.exec_module(module)
    return module


select_tests = _load()


def test_reverse_graph_maps_source_files_to_test_files() -> None:
    """改了 `core/library_text.py` → 引用它的测试文件必须在图里。"""
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    dependents = graph.get("src/core/library_text.py")
    assert dependents, "core/library_text.py 的依赖测试没被建出来"
    assert "test_library_text_refs.py" in dependents


def test_graph_keys_use_src_prefix() -> None:
    """图的 key 必须带 `src/` 前缀，与 git diff 的路径一致。

    两处格式不一致会让整个依赖图**查不中**——表现是「只跑守卫、漏掉主测试
    文件」，而且不报错。
    """
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    assert graph, "依赖图是空的"
    assert all(key.startswith("src/") for key in graph), "有 key 没带 src/ 前缀"


def test_affected_sources_does_not_propagate_downward() -> None:
    """不向下传：改了一个文件，不等于改它调用者的行为。

    向下传会让 `core/registry.py` 的改动扩散到全部源文件——那等于全量，
    这个工具就白做了。
    """
    modules = select_tests._source_modules()  # type: ignore[attr-defined]
    assert "core.registry" in modules
    source = modules["core.registry"]
    rel = f"src/{source.relative_to(select_tests.SRC).as_posix()}"  # type: ignore[attr-defined]

    # 纯新增（无删除行）→ 不带上游依赖者
    hunks = {rel: [("+", "def remove(self): ...")]}
    affected = select_tests._affected_sources(  # type: ignore[attr-defined]
        [rel], select_tests._build_reverse_graph(), hunks  # type: ignore[attr-defined]
    )
    assert rel in affected
    assert len(affected) == 1, f"纯扩展改动却带出了上游：{sorted(affected)}"


def test_pure_addition_is_not_semantic_change() -> None:
    """只新增 → 不是语义变化；有删除行 → 是。"""
    assert not select_tests._is_semantic_change("src/x.py", [("+", "def f(): ...")])  # type: ignore[attr-defined]
    assert select_tests._is_semantic_change("src/x.py", [("-", "old()"), ("+", "new()")])  # type: ignore[attr-defined]


def test_public_entry_point_addition_counts_as_semantic() -> None:
    """新增 `register_*` / `reset_*` 这类框架入口 → 算语义变化。

    那类东西一改就是全局口径（注入口目录、端口），不能按「局部扩展」处理。
    """
    hunks = [("+", "def register_thing() -> None: ...")]
    assert select_tests._is_semantic_change("src/x.py", hunks)  # type: ignore[attr-defined]


def test_structural_changes_are_classified_as_full() -> None:
    """端口目录 / 装配声明 / 迁移 → 必须全量。"""
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    for changed in (
        ["src/api/ports.py"],
        ["src/api/assembly.py"],
        ["src/core/exceptions.py"],
        ["src/generation/runner.py"],
        ["pyproject.toml"],
        ["migrations/versions/abc_add_x.py"],
        ["src/modules/library/spec.py"],
    ):
        level = select_tests._classify(changed, set(), graph, {})  # type: ignore[attr-defined]
        assert level == "full", f"{changed} 应当升级到全量，实际 {level}"


def test_business_module_change_is_targeted() -> None:
    """业务模块的局部改动 → targeted，不是 framework。"""
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    changed = ["src/modules/library/tags.py"]
    hunks = {changed[0]: [("-", "old"), ("+", "new")]}
    level = select_tests._classify(changed, set(), graph, hunks)  # type: ignore[attr-defined]
    assert level == "targeted"


def test_framework_change_with_semantic_edit_is_framework_level() -> None:
    """框架层且改了已有语义 → framework（依赖图 + 全部守卫）。"""
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    changed = ["src/core/metrics.py"]
    hunks = {changed[0]: [("-", "old"), ("+", "new")]}
    level = select_tests._classify(changed, set(), graph, hunks)  # type: ignore[attr-defined]
    assert level == "framework"


def test_framework_pure_addition_stays_targeted() -> None:
    """框架层但**只是新增** → 仍是 targeted。

    给 `NamedRegistry` 加个 `remove()` 方法不该让每次小改都退化成全量。
    """
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    changed = ["src/core/registry.py"]
    hunks = {changed[0]: [("+", "def remove(self, name: str) -> bool: ...")]}
    level = select_tests._classify(changed, set(), graph, hunks)  # type: ignore[attr-defined]
    assert level == "targeted"


def test_model_router_is_not_treated_as_contract_file() -> None:
    """`infra/ai/router.py` 是**模型路由**，跟 OpenAPI 契约无关。

    按文件名一刀切会让它命中「改了契约」，误伤成 contract 级。
    """
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    changed = ["src/infra/ai/router.py"]
    hunks = {changed[0]: [("-", "old"), ("+", "new")]}
    level = select_tests._classify(changed, set(), graph, hunks)  # type: ignore[attr-defined]
    assert level == "framework"  # infra 是框架层，不是 contract


@pytest.mark.parametrize(
    "hint",
    ["src/modules/library/router.py", "src/modules/image/schema.py"],
)
def test_module_router_and_schema_are_contract_level(hint: str) -> None:
    """模块的路由 / 出参 schema 改了 → contract（四份生成物要重导）。"""
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    hunks = {hint: [("-", "old"), ("+", "new")]}
    level = select_tests._classify([hint], set(), graph, hunks)  # type: ignore[attr-defined]
    assert level == "contract"
