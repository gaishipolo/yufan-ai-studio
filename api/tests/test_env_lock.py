"""`.env` 读-改-写锁的基本行为。"""

from __future__ import annotations

import threading
import time

from infra.env_lock import env_write_lock


def test_锁可以反复获取与释放() -> None:
    with env_write_lock():
        pass
    with env_write_lock():
        pass


def test_两个线程不会同时进入临界区() -> None:
    order: list[str] = []
    ready = threading.Barrier(2)

    def worker(name: str) -> None:
        ready.wait()
        with env_write_lock():
            order.append(f"{name}-in")
            time.sleep(0.05)
            order.append(f"{name}-out")

    threads = [
        threading.Thread(target=worker, args=("a",)),
        threading.Thread(target=worker, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # 两段临界区不能交错：只可能是 a 全进 b 后，或反过来。
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )

def test_模型运维页写入也使用同一把锁(monkeypatch) -> None:
    from contextlib import contextmanager

    from modules.model_driver import runtime_config

    calls: list[int] = []

    @contextmanager
    def spy():
        calls.append(1)
        yield

    monkeypatch.setattr(runtime_config, "env_write_lock", spy)
    runtime_config.apply_updates({})
    assert calls == [1]
