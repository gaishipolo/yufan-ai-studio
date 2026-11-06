"""启动方 ⇄ LibTV 监督进程的参数协议（A4）。

旧写法是位置参数：`[stage, run, legacy, node, result]` 靠 `sys.argv[1:6]` 顺序对齐。
加一个参数就得两侧同时改，改漏了不是报错，而是**读串位**——某个字段拿到隔壁字段的值，
故障现象离原因很远。现在改成 stdin 的 JSON + 版本号，两侧各自解析、各自校验。
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
    written: dict[str, Any] = {}

    def fake_supervise(stage: str, run: Any, legacy: Any, node: str) -> ExecutorResult:
        seen.update(stage=stage, run=run, legacy=legacy, node=node)
        return ExecutorResult(code="OK", stage=stage)

    class FakeStorage:
        def write(self, path: Any, value: object) -> None:
            written.update(path=path, value=value)

    monkeypatch.setattr(libtv_process, "supervise", fake_supervise)
    monkeypatch.setattr(libtv_process, "get_archive_storage", lambda: FakeStorage())
    _feed(
        monkeypatch,
        {
            "version": LIBTV_PROCESS_PROTOCOL,
            "stage": "submit",
            "run": str(tmp_path / "run"),
            "legacyRoot": str(tmp_path / "legacy"),
            "node": "node",
            "result": str(tmp_path / "out.json"),
        },
    )

    libtv_process.main()

    assert seen["stage"] == "submit"
    assert seen["legacy"] == tmp_path / "legacy"
    assert seen["node"] == "node"
    assert written["path"] == tmp_path / "out.json"
    assert written["value"]["code"] == "OK"


def test_node_has_a_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """可选参数必须有默认值：新增参数不该要求启动方同步改。"""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        libtv_process,
        "supervise",
        lambda stage, run, legacy, node: seen.update(node=node) or ExecutorResult(stage=stage),
    )
    monkeypatch.setattr(
        libtv_process, "get_archive_storage", lambda: SimpleNamespace(write=lambda *a: None)
    )
    _feed(
        monkeypatch,
        {
            "version": 1,
            "stage": "poll",
            "run": str(tmp_path / "run"),
            "legacyRoot": str(tmp_path / "legacy"),
            "result": str(tmp_path / "out.json"),
        },
    )

    libtv_process.main()

    assert seen["node"] == "node"


def test_unknown_protocol_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """版本不认识就退出码 2 —— 让启动方报「协议不一致」，而不是等一个永远不来的结果文件。"""
    _feed(monkeypatch, {"version": 999, "stage": "submit"})
    with pytest.raises(SystemExit) as exc:
        libtv_process.main()
    assert exc.value.code == 2


def test_garbage_input_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _feed(monkeypatch, b"submit /path legacy node result")
    with pytest.raises(SystemExit) as exc:
        libtv_process.main()
    assert exc.value.code == 2
