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
    )


def test_render_produces_the_documented_file_set() -> None:
    """文档说 6 个文件（加 --generation 是 7 个），实际必须一致。"""
    assert len(render(_plan(with_generation=False))) == 6
    assert set(render(_plan(with_generation=True))) == {
        "__init__.py",
        "models.py",
        "schema.py",
        "service.py",
        "dependencies.py",
        "router.py",
        "pipeline.py",
    }


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
