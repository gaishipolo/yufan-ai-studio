"""启动方 ⇄ LibTV 监督进程的参数协议（A4）。

旧写法是位置参数：`[stage, run, legacy, node, result]` 靠 `sys.argv[1:6]` 顺序对齐。
加一个参数就得两侧同时改，改漏了不是报错，而是**读串位**——某个字段拿到隔壁字段的值，
故障现象离原因很远。现在改成 stdin 的 JSON + 版本号，两侧各自解析、各自校验。

v2 起 payload 不再带适配器目录：固定版适配器随代码入库，监督进程按自身位置计算。
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from infra.ai import libtv_process
from infra.ai.libtv_state import LIBTV_PROCESS_PROTOCOL, ExecutorResult


def _feed(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any] | bytes) -> None:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))


def test_main_parses_payload_and_reports_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    seen: dict[str, Any] = {}

    def fake_supervise(stage: str, run: Any, node: str) -> ExecutorResult:
        seen.update(stage=stage, run=run, node=node)
        return ExecutorResult(code="OK", stage=stage)

    monkeypatch.setattr(libtv_process, "supervise", fake_supervise)
    _feed(
        monkeypatch,
        {
            "version": LIBTV_PROCESS_PROTOCOL,
            "stage": "submit",
            "run": str(tmp_path / "run"),
            "node": "node",
            "result": str(tmp_path / "out.json"),
        },
    )

    libtv_process.main()

    assert seen["stage"] == "submit"
    assert seen["run"] == tmp_path / "run"
    assert seen["node"] == "node"
    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert written["code"] == "OK"


def test_node_has_a_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """可选参数必须有默认值：新增参数不该要求启动方同步改。"""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        libtv_process,
        "supervise",
        lambda stage, run, node: seen.update(node=node) or ExecutorResult(stage=stage),
    )
    monkeypatch.setattr(
        libtv_process, "get_archive_storage", lambda: SimpleNamespace(write=lambda *a: None)
    )
    _feed(
        monkeypatch,
        {
            "version": LIBTV_PROCESS_PROTOCOL,
            "stage": "poll",
            "run": str(tmp_path / "run"),
            "result": str(tmp_path / "out.json"),
        },
    )

    libtv_process.main()

    assert seen["node"] == "node"


def test_v1_payload_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """旧形状（带 legacyRoot）必须先被版本拦住，不能拿着半个参数包去跑任务。"""
    _feed(
        monkeypatch,
        {
            "version": LIBTV_PROCESS_PROTOCOL - 1,
            "stage": "submit",
            "run": str(tmp_path / "run"),
            "legacyRoot": str(tmp_path / "legacy"),
            "result": str(tmp_path / "out.json"),
        },
    )
    with pytest.raises(SystemExit) as exc:
        libtv_process.main()
    assert exc.value.code == 2


def test_unknown_protocol_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """版本不认识就退出码 2 —— 让启动方报「协议不一致」，而不是等一个永远不来的结果文件。"""
    _feed(monkeypatch, {"version": 999, "stage": "submit"})
    with pytest.raises(SystemExit) as exc:
        libtv_process.main()
    assert exc.value.code == 2


def test_garbage_input_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _feed(monkeypatch, b"submit /path node result")
    with pytest.raises(SystemExit) as exc:
        libtv_process.main()
    assert exc.value.code == 2


def test_executor_env_drops_delete_semantics_shims(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主带 WorkBuddy 垫片时，执行器子进程不能继承它。

    垫片通过 `PYTHONPATH` 注入 `sitecustomize`，把 `unlink` 换成「进回收站 +
    批量删除守卫」；执行器清 `records/worker.lock` 时撞上守卫会直接退出，任务被写成
    「LibTV 查询中断：safe-delete 安全守卫……」而上游其实一切正常。
    """
    from infra.ai import libtv

    monkeypatch.setenv("PYTHONPATH", r"D:/WorkBuddy/resources/app.asar.unpacked/cli/vendor/shim")
    monkeypatch.setenv("NODE_OPTIONS", "--require shim.cjs")
    monkeypatch.setenv("CODEBUDDY_SAFE_DELETE_ENABLED", "1")
    monkeypatch.setenv("JS_REVERSE_KEEP_ME", "1")

    env = libtv.supervisor_env()

    assert "PYTHONPATH" not in env
    assert "NODE_OPTIONS" not in env
    assert "CODEBUDDY_SAFE_DELETE_ENABLED" not in env
    # 其余变量照旧继承：执行器要靠 PATH 找 node
    assert env["JS_REVERSE_KEEP_ME"] == "1"
    assert env.get("PATH")
