"""命令行入口的冷启动行为。

**为什么要用子进程测**：这里出过的问题恰恰是「导入顺序」——`core.permissions.catalog`
是模块声明时填的，而 CLI 不走 `create_app()`。在同一个测试进程里，前面的用例早就
把模块发现跑过了，catalog 是满的，`sync-roles --prune-snapshot` 看起来完全正常；
换成一个干净的 CLI 进程，catalog 是空集，那条命令会把整张快照抹成 `[]`，
下一次启动于是把管理员手工移除的权限全部当「新增」补回来。

同一个判断在进程内永远测不出来，只能起一个真进程。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "cli", *args],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )


def test_prune_snapshot_in_a_cold_process_keeps_the_catalog() -> None:
    """`--prune-snapshot --dry-run` 在干净进程里不得报出「移除 N 个权限点」。

    用 dry-run 是因为：这条用例要验的是**读到的目录对不对**，不是写库；
    真跑一次会在测试库上与父进程抢写锁，收益还不大。
    """
    result = _run("sync-roles", "--prune-snapshot", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "快照移除" not in result.stdout, (
        "CLI 里权限目录是空的：`sync-roles` 忘了先 `discover_modules()`，"
        "`--prune-snapshot` 会把整张快照抹掉。输出：\n" + result.stdout + result.stderr
    )
    # 目录真被读到了才会有这句（或者有「+权限点」）
    assert "内置角色已是最新" in result.stdout or "快照补入" in result.stdout


def test_meta_command_lists_keys() -> None:
    """`app meta` 是 `app_meta` 的读入口，至少得能读到权限目录快照。"""
    result = _run("meta")

    assert result.returncode == 0, result.stderr
    assert "permissions.catalog" in result.stdout
    assert "v" in result.stdout, "没有打印版本号"


def test_meta_command_shows_history_for_a_key() -> None:
    """传键时打轨迹：谁、第几版、一句话摘要。"""
    result = _run("meta", "permissions.catalog")

    assert result.returncode == 0, result.stderr
    assert "permissions.catalog" in result.stdout
    assert "最近写入" in result.stdout or "不存在" in result.stdout
