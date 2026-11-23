"""执行器成片的落盘位置与输出地址格式（`infra/executor_output.py`）。

## 这组测试在防什么

驱动（`bailian` / `libtv`）写成片、框架（`modules/task/pipeline.py`）搬成片，
两边靠**同一个字面量**对齐。漂移的后果不是报错，而是**静默丢件**：

```
驱动写  outputs/generated.mp4
框架找  outputs/final.mp4   → 找不到
→ 任务显示成功，但资产库里一条视频都没有
```

历史上真出现过「图片入库 6 条、视频 0 条」。所以这里的测试重点不是「函数返回
什么」，而是**驱动产出的地址能不能被框架的识别逻辑认出来**。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from infra.executor_output import (
    EXECUTOR_OUTPUT_RELATIVE,
    RUN_OUTPUT_URL,
    executor_output_url,
)

_SRC = Path(__file__).resolve().parent.parent / "src"


def test_relative_path_is_the_expected_one() -> None:
    """落盘相对路径：执行器写死的名字，改了要同步改 `canvas-run.mjs`。"""
    assert EXECUTOR_OUTPUT_RELATIVE == "outputs/generated.mp4"


def test_url_format() -> None:
    run_id = "a" * 32
    assert executor_output_url(run_id) == (
        f"/api/v1/templates/replacement-runs/{run_id}/output"
    )


def test_framework_can_recognize_the_driver_output() -> None:
    """**核心**：驱动产出的地址必须被框架的识别正则认出来，且捕获组是 run id。

    这一条断了 = 框架不搬成片 = 静默丢件。
    """
    run_id = "0123456789abcdef0123456789abcdef"
    match = RUN_OUTPUT_URL.search(executor_output_url(run_id))

    assert match is not None, "框架认不出驱动产出的地址，成片会被当直链丢掉"
    assert match.group(1) == run_id


def test_recognizer_ignores_other_urls() -> None:
    """不是这种形状的地址（供应商直链、对象 key）不能被误认。"""
    for url in (
        "https://cos-platform-outputs.oss-cn-beijing.aliyuncs.com/xxx.mp4",
        "videos/abc123.mp4",
        "/api/v1/templates/replacement-runs/not-a-uuid/output",
        "/api/v1/templates/replacement-runs/" + "a" * 32 + "/outputs",
    ):
        assert RUN_OUTPUT_URL.search(url) is None, f"不该被认成执行器输出：{url}"


def test_drivers_and_framework_share_one_source() -> None:
    """**防漂移守卫**：驱动与框架的**代码**里都不得再硬编码这两个字面量。

    任何一处把字面量抄回去，就等于开了个新的「写这边、改那边」的口子。

    只看**代码**、不看文档字符串：模块 docstring 里说明「成片落在
    `outputs/generated.mp4`」是合理的叙述，硬编码才是漂移。所以这里用 AST
    把 docstring 摘掉再查剩下的字符串字面量。
    """
    offenders: list[str] = []
    targets = [
        *_SRC.glob("modules/model_driver/drivers/*.py"),
        _SRC / "modules" / "task" / "pipeline.py",
    ]
    for path in targets:
        source = path.read_text(encoding="utf-8")
        for lineno, literal in _code_string_literals(source):
            for needle in ("outputs/generated.mp4", "/replacement-runs/"):
                if needle in literal:
                    offenders.append(f"{path.name}:{lineno}: {needle}")

    assert not offenders, (
        "这些地方把字面量硬编码回去了，必须改用 infra.executor_output："
        + ", ".join(offenders)
    )


def _code_string_literals(source: str) -> list[tuple[int, str]]:
    """返回 (行号, 字符串字面量) 列表，**排除 docstring**。

    docstring = 模块 / 类 / 函数的第一个字符串表达式语句。叙述性文字里提到
    路径是合理的（还得让人看得懂），要拦的是「代码里把值写死」。
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.append((node.lineno, node.value))
    return found


def test_framework_module_uses_the_shared_constants() -> None:
    """框架侧的落盘路径与识别正则都取自共享件，不是各自一份。"""
    source = (_SRC / "modules" / "task" / "pipeline.py").read_text(encoding="utf-8")

    assert "from infra.executor_output import" in source
    assert re.search(r"_EXECUTOR_OUTPUT\s*=\s*EXECUTOR_OUTPUT_RELATIVE", source)
    assert re.search(r"_RUN_OUTPUT_URL\s*=\s*RUN_OUTPUT_URL", source)
