"""脚手架生成物必须「生成即能跑」。

这里检查的不是模板文案，而是两件会让新人卡半天的事：
1. 渲染出来的每个文件都是合法 Python（模板字符串少个引号就废）
2. `--generation` 生成的 Store 实现了 `GenerationStore` 的全部方法
   （少一个 `mark_submitting`，生成任务一提交就 AttributeError）
"""

from __future__ import annotations

import ast

import pytest

from generation.store import GenerationStore
from scaffold import ModulePlan, render


def _plan(*, with_generation: bool) -> ModulePlan:
    return ModulePlan(
        name="music",
        label="音乐",
        perm="music",
        slot=4,
        with_generation=with_generation,
        # 带流水线时必须显式说清能力：模板曾经写死 VIDEO，于是「生图模块」
        # 生成出来会走视频供应商，而协议守卫照样绿
        capability="audio" if with_generation else "",
    )


def test_generation_without_capability_is_rejected() -> None:
    """`--generation` 不写 `--capability` 直接拒绝落盘。

    为什么不能给默认值：capability 决定这个模块的请求交给哪一类供应商。
    写错了不会在启动时报错（pipeline 协议是对的），只会在第一次真实提交时
    把图发给视频供应商——错误发生在最贵的那一步。
    """
    from scaffold import ScaffoldError, validate

    with pytest.raises(ScaffoldError, match="capability"):
        validate(ModulePlan(name="music", label="音乐", perm="music", slot=4, with_generation=True))


def test_capability_without_generation_is_rejected() -> None:
    """光有 `--capability` 没有 `--generation` 是误用，别默默忽略。"""
    from scaffold import ScaffoldError, validate

    with pytest.raises(ScaffoldError, match="capability"):
        validate(
            ModulePlan(
                name="music", label="音乐", perm="music", slot=4, capability="audio"
            )
        )


def test_render_produces_the_documented_file_set() -> None:
    """基础 7 个文件（含 errors.py），加 --generation 是 8 个。"""
    assert len(render(_plan(with_generation=False))) == 7
    assert set(render(_plan(with_generation=True))) == {
        "__init__.py",
        "models.py",
        "schema.py",
        "service.py",
        "dependencies.py",
        "router.py",
        "errors.py",
        "pipeline.py",
    }


@pytest.mark.parametrize("slot", [1, 4, 9])
def test_slot_actually_reaches_the_generated_errors(slot: int) -> None:
    """`--slot` 必须真的写进产物。

    它以前是「三重承诺、零效果」的死参数：help 里写着、`validate()` 校验、
    文档要求先占号段，但模板里没有任何一处引用它（R4-02）。
    """
    plan = ModulePlan(name="music", label="音乐", perm="music", slot=slot)
    source = render(plan)["errors.py"]

    assert f'slot={slot}' in source
    # 错误码用 ModuleErrors 拼，不手写数字——手写的号段迟早和别人撞
    assert "ModuleErrors(" in source


def test_slot_zero_is_rejected() -> None:
    """不传 --slot 时默认 0，必须当场报错而不是默默用 3（3 是 task 的号段）。"""
    from scaffold import ScaffoldError, validate

    with pytest.raises(ScaffoldError, match="slot"):
        validate(ModulePlan(name="music", label="音乐", perm="music", slot=0))


def test_sharing_a_slot_is_allowed_when_codes_do_not_clash(tmp_path) -> None:
    """号段可以共用：9 个号段装不下第 10 个模块，`template`/`shot` 就共用一个。

    老模块只占了 40921 之外的码位（这里一个都没占），新模块的 `40921`
    在 slot 2 里是空的，应当放行。
    """
    from scaffold import create_module

    modules = tmp_path / "src" / "modules" / "identity"
    modules.mkdir(parents=True)
    (modules / "errors.py").write_text(
        'errors = ModuleErrors("identity", slot=2)\n'
        "\n"
        "\n"
        "class CredentialInvalid(AppError):\n"
        "    code = errors.code(401, 1)\n",
        encoding="utf-8",
    )

    written = create_module(
        ModulePlan(name="music", label="音乐", perm="music", slot=2), tmp_path
    )
    assert {p.name for p in written} >= {"errors.py", "router.py"}


def test_clashing_code_is_detected_before_writing(tmp_path) -> None:
    """同一个码值不能两个模块都用，否则前端拿到 40921 分不清是谁失败。

    模板里的示例码是 `errors.code(409, 1)`；slot 2 下它就是 40921，
    老模块已占用时必须拦住，且一个文件都不落盘。
    """
    from scaffold import ScaffoldError, create_module

    modules = tmp_path / "src" / "modules" / "identity"
    modules.mkdir(parents=True)
    (modules / "errors.py").write_text(
        'errors = ModuleErrors("identity", slot=2)\n'
        "\n"
        "\n"
        "class IdentityBusy(AppError):\n"
        "    code = errors.code(409, 1)\n",
        encoding="utf-8",
    )

    with pytest.raises(ScaffoldError, match=r"40921（identity）"):
        create_module(
            ModulePlan(name="music", label="音乐", perm="music", slot=2), tmp_path
        )

    assert not (tmp_path / "src" / "modules" / "music").exists()


def test_generated_router_returns_501_not_500() -> None:
    """占位接口必须抛 `NotImplementedFeature`（501）。

    裸抛 `NotImplementedError` 会被全局异常处理器当成未捕获异常，返回 500，
    前端会显示「服务器内部错误」——和文档承诺的「501 + 待实现文案」不符。
    """
    source = render(_plan(with_generation=False))["router.py"]
    assert "NotImplementedFeature" in source
    assert "raise NotImplementedError(" not in source


def test_generated_module_registers_its_pipeline() -> None:
    """`--generation` 生成的模块必须在 `__init__.py` 里做副作用导入。

    流水线靠 import 时执行 `@register_pipeline` 注册；漏掉这一行，
    `dispatch(kind, ...)` 直接报「未注册」——但代码看起来一切正常。
    """
    with_gen = render(_plan(with_generation=True))["__init__.py"]
    without_gen = render(_plan(with_generation=False))["__init__.py"]

    assert "from modules.music import pipeline" in with_gen
    assert "pipeline" not in without_gen


@pytest.mark.parametrize("with_generation", [False, True])
def test_every_rendered_file_compiles(with_generation: bool) -> None:
    for filename, source in render(_plan(with_generation=with_generation)).items():
        compile(source, filename, "exec")


def test_generated_store_implements_the_whole_protocol() -> None:
    source = render(_plan(with_generation=True))["pipeline.py"]
    tree = ast.parse(source)

    store_classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith("Store")
    ]
    assert store_classes, "pipeline.py 里没有 Store 类"

    implemented = {
        node.name
        for node in store_classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    required = {name for name in dir(GenerationStore) if not name.startswith("_")}

    missing = required - implemented
    assert not missing, f"脚手架生成的 Store 缺少协议方法：{sorted(missing)}"


def _declared_permissions(source: str) -> set[str]:
    codes: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "PermissionSpec" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            codes.add(first.value)
    return codes


def _enforced_permissions(sources: list[str]) -> set[str]:
    """生成物里 `require("...")` 用到的码。"""
    codes: set[str] = set()
    for source in sources:
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "require":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    codes.add(arg.value)
    return codes


@pytest.mark.parametrize("with_generation", [False, True])
def test_generated_module_passes_the_permission_coverage_guard(
    with_generation: bool,
) -> None:
    """脚手架声明了哪个权限点，生成物里就要有对应的 `require(...)`。

    这条守的是**模板与守卫的一致性**：`tests/test_permission_coverage.py` 只扫
    已经落盘的 `src/modules/*`，对模板本身没有约束。模板里多声明一个权限点、
    或者删掉一行 `${Name}UpdateDep`，`app new-module` 生成出来的模块会直接红在
    「声明了没人用」这条新守卫上——而发现这个错误的人是刚用完脚手架的新人。

    模板生成的是 CRUD 骨架，四个 `require(...)` 都写在 `dependencies.py` 里
    （路由只挂了 read/create，update/delete 留给业务自己接）。所以扫描范围
    必须包含 `dependencies.py`，否则会误报 `:update` / `:delete`。
    """
    files = render(_plan(with_generation=with_generation))
    declared = _declared_permissions(files["__init__.py"])
    enforced = _enforced_permissions(
        [files["dependencies.py"], files["router.py"]]
    )

    assert declared, "模板里一个 PermissionSpec 都没有，扫描口径可能坏了"
    missing = sorted(declared - enforced)
    assert not missing, (
        f"脚手架声明了但生成物里没有判定点：{missing}。"
        "新模块一落地就会撞上「声明了没人用」的守卫"
    )


def test_wrong_root_is_rejected_instead_of_growing_an_orphan_tree(tmp_path) -> None:
    """`--root` 指错地方必须当场报错，而不是造出一棵永远不会被加载的目录。

    真实事故：在仓库根执行 `api/.venv/Scripts/app.exe new-module export ...`。
    `--root` 默认 `.`，而这个仓库的 Python 源码根是 `api/`，于是生成了
    `<repo>/src/modules/export/`——七个文件、语法正确、`app modules` 里查无此模块。

    这一类的共同形态是**失败时不发声**：目录造出来了，命令退出码是 0，
    唯一的症状是「模块没被加载」，而排查方向会先跑到 discovery 上去。
    """
    from scaffold import ScaffoldError, create_module

    with pytest.raises(ScaffoldError, match="src/modules"):
        create_module(
            ModulePlan(name="music", label="音乐", perm="music", slot=4),
            tmp_path,  # 空目录：没有 src/modules
        )
    # 报错之后不能留下半棵树
    assert not (tmp_path / "src").exists()


def test_a_proper_project_root_still_generates(tmp_path) -> None:
    """守卫不能把正常路径也一起挡了：`<root>/src/modules` 存在就该照常生成。"""
    from scaffold import create_module

    (tmp_path / "src" / "modules").mkdir(parents=True)
    written = create_module(
        ModulePlan(name="music", label="音乐", perm="music", slot=4), tmp_path
    )

    assert len(written) == 7
    assert all(path.is_file() for path in written)
    assert written[0].parent == tmp_path / "src" / "modules" / "music"


@pytest.mark.parametrize("with_generation", [False, True])
def test_generated_init_survives_ruff_without_hand_editing(with_generation: bool) -> None:
    """生成物必须直接过 lint。

    模板里 `${generation_import}` 在不带 `--generation` 时是空串，而它后面还有
    一个空行——于是导入块与 `module = ModuleSpec(...)` 之间变成**两个**空行，
    `ruff check` 报 I001。新人的第一步就撞红，而他的处理方式不会是「报模板 bug」，
    是手改一份本来就该重新生成的文件；下次重新生成又回来。
    """
    source = render(_plan(with_generation=with_generation))["__init__.py"]

    head, separator, _ = source.partition("module = ModuleSpec(")
    assert separator, "模板里找不到 module = ModuleSpec("
    assert head.endswith("\n\n"), "导入块和 module 之间必须留一个空行"
    assert not head.endswith("\n\n\n"), "出现了多余空行（ruff I001）"
    assert "\n\n\n" not in source, "文件里不该有连续两个空行"


def test_generation_import_is_sorted_before_the_router_import() -> None:
    """副作用导入必须排在 router 之前。

    `from modules.x import pipeline` 与 `from modules.x.router import router`
    是前缀关系，顺序反了 isort 报 I001（按模块路径排序，短的在前）。
    这条只有带 `--generation` 才触发，正好是最少被走的那条路——
    所以模板里两个空行的问题当时只暴露了一半。
    """
    source = render(_plan(with_generation=True))["__init__.py"]
    pipeline_at = source.index("import pipeline")
    router_at = source.index("from modules.music.router import router")
    assert pipeline_at < router_at
