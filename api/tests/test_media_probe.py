"""媒体探测的错误分类：文件坏是 400，服务坏才是 500。

`inspect()` 是上传链路的第一道门。它抛 `BadRequest` 时用户看到「请使用有效的
MP4 视频」；抛别的类型就落进兜底分支变成 500「服务器内部错误」——同一件事，
两种说法，一个能自助解决，一个只能来问我们。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.exceptions import BadRequest
from infra import media_probe


def _fake_mp4(path: Path) -> Path:
    """够过「非空 + 不超过 300MB」检查的假文件；内容不重要，探测会被打桩。"""
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    return path


def test_probe_timeout_is_reported_as_a_bad_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg 探测超时是「这个文件我们解析不了」，不是服务端故障。

    300MB 的畸形文件、卡在共享盘上的半成品、需要联网取索引的容器，都可能让
    `ffmpeg -i` 跑满 30 秒。超时抛的是 `subprocess.TimeoutExpired`，它不是
    `BadRequest`，于是用户拿到 500——而真正该说的话是「请换一个文件」。
    """
    source = _fake_mp4(tmp_path / "clip.mp4")

    def slow_probe(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

    monkeypatch.setattr(media_probe.subprocess, "run", slow_probe)

    with pytest.raises(BadRequest, match="超时"):
        media_probe.inspect(source, ".mp4")


def test_unparsable_container_is_a_bad_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对照组：真正「解析不出来」也是 400。两条路径的错误码必须一致。"""

    class Result:
        returncode = 1
        stderr = "clip.mp4: Invalid data found when processing input"

    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **k: Result())

    with pytest.raises(BadRequest, match="无法解析"):
        media_probe.inspect(_fake_mp4(tmp_path / "bad.mp4"), ".mp4")
