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
    """端口目录 / 装配声明 / 迁移 → 必须全量。

    装配声明指的是 `src/modules/<mod>/__init__.py` 里的 `ModuleSpec(...)`。
    （旧判据写的是 `modules/*/spec.py` —— 那个路径只存在于
    `model_driver/spec.py`，一份开发标准文档，等于这条规则从来没生效过。）
    """
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    for changed in (
        ["src/api/ports.py"],
        ["src/api/assembly.py"],
        ["src/core/exceptions.py"],
        ["src/generation/runner.py"],
        ["pyproject.toml"],
        ["migrations/versions/abc_add_x.py"],
        ["src/modules/library/__init__.py"],
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


# --------------------------------------------------------------------------- #
# 2026-11-15 修复：黑盒 HTTP 测试在静态依赖图里是隐形的
#
# 资产库那批测试打的是真实接口（`client.post(f"{api}/library/tags", ...)`），
# 它们**不 import 任何源模块** → 静态图看不见 → 改了 `library/tags.py`
# 却不跑 `test_library_tags.py`。工具自己的 docstring 举的就是这个例子。
# --------------------------------------------------------------------------- #
def _selection_for(changed: list[str]) -> tuple[str, set[str]]:
    """跑一遍真实的判据链，返回（级别, 选中的测试文件集合）。"""
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    hunks = {path: [("-", "old"), ("+", "new")] for path in changed}
    affected = select_tests._affected_sources(changed, graph, hunks)  # type: ignore[attr-defined]
    level = select_tests._classify(changed, affected, graph, hunks)  # type: ignore[attr-defined]
    selected = select_tests._select_tests(changed, affected, graph, level)  # type: ignore[attr-defined]
    return level, selected


def test_black_box_http_tests_are_selected_by_module_path() -> None:
    """改 `modules/library/tags.py` → 必须选中 `test_library_tags.py`。

    这条是工具 docstring 自己许下的承诺（"改了 tags.py 就会命中
    test_library_tags.py"）。修之前它是假的：黑盒测试不 import 源模块，
    静态图里没有边。
    """
    _, selected = _selection_for(["src/modules/library/tags.py"])
    assert "test_library_tags.py" in selected


def test_module_change_selects_whole_module_http_tests() -> None:
    """改模块里任何文件 → 打这个模块路径的测试都要跑。

    粒度只能是模块：测试打的是 `/library/tags`，它依赖 library 里的哪个
    文件，静态分析看不出来。
    """
    _, selected = _selection_for(["src/modules/library/service.py"])
    for name in ("test_library_tags.py", "test_library_sharing.py", "test_library_upload.py"):
        assert name in selected, f"{name} 没被选中"


def test_other_module_change_does_not_pull_library_tests() -> None:
    """改别的模块不该把资产库的测试也拉进来（别把工具做成全量）。"""
    _, selected = _selection_for(["src/modules/shot/service.py"])
    assert "test_library_tags.py" not in selected


def test_module_spec_change_triggers_full() -> None:
    """改模块装配声明（`__init__.py` 里的 `ModuleSpec`）→ 升级全量。

    旧判据写的是 `modules/*/spec.py` —— 那个路径只存在于 `model_driver/spec.py`
    （一份开发标准文档），所以这条规则从来没生效过：改真正的装配声明既不升
    full，也不带上装配 / 权限对账那两道守门测试。
    """
    graph = select_tests._build_reverse_graph()  # type: ignore[attr-defined]
    changed = ["src/modules/library/__init__.py"]
    hunks = {changed[0]: [("-", "old"), ("+", "new")]}
    level = select_tests._classify(changed, set(), graph, hunks)  # type: ignore[attr-defined]
    assert level == "full"

    selected = select_tests._select_tests(changed, set(), graph, "full")  # type: ignore[attr-defined]
    assert "test_assembly.py" in selected
    assert "test_permission_coverage.py" in selected


def test_deleted_file_is_treated_as_semantic_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """删文件是最强的语义变更 → 必须算「改了语义」，并带上上游依赖者。

    修之前：`+++ /dev/null`（删除）没有专门处理，`current` 还指着上一个文件，
    被删的文件自己一条 hunk 都没有 → 判定成「纯新增」→ 不带上游。
    """
    diff = (
        "diff --git a/api/src/core/aaa.py b/api/src/core/aaa.py\n"
        "index 111..222 100644\n"
        "--- a/api/src/core/aaa.py\n"
        "+++ b/api/src/core/aaa.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/api/src/core/zzz.py b/api/src/core/zzz.py\n"
        "deleted file mode 100644\n"
        "index 333..000 100644\n"
        "--- a/api/src/core/zzz.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-import os\n"
        "-print(1)\n"
    )
    monkeypatch.setattr(select_tests, "_run_git", lambda args: diff)  # type: ignore[attr-defined]
    hunks = select_tests._diff_hunks(None)  # type: ignore[attr-defined]
    assert "src/core/zzz.py" in hunks, "被删的文件没有进 hunks"
    assert select_tests._is_semantic_change("src/core/zzz.py", hunks["src/core/zzz.py"])  # type: ignore[attr-defined]
    # 被删文件的删除行不许算到上一个文件头上
    assert all(text != "import os" for _, text in hunks["src/core/aaa.py"])
