"""请求级事务边界：`get_session` 依赖在异常时回滚并把异常抛出去。

## 这条依赖真正守的是什么

`async with factory() as session` 退出时 `Session.close()` 本来就会丢弃未提交的
事务，所以「忘记 rollback 导致脏数据落库」并不成立。真正会出事的是另外两件：

1. **异常被吞掉**。`except Exception: await session.rollback()` 少了 `raise`，
   FastAPI 会认为依赖正常结束、继续返回响应——客户端拿到 200，而实际上
   业务什么也没做成。日志里只有一条 warning，没有任何 5xx 可供告警。
2. **连接带锁归池**。PostgreSQL 里未回滚的事务会一直持有行锁直到连接被复用，
   `close()` 虽然也会回滚，但那是「归还时」而不是「出错时」——高并发下这段
   时间差正是死锁的温床。

变异扫描把 `await session.rollback()` 删掉后 36 条测试全绿，说明这层边界
此前没有直接测试。
"""

from __future__ import annotations

import pytest

from db.session import get_session


async def _open() -> tuple[object, object]:
    """拿到底层会话，并把生成器留在 yield 处。"""
    generator = get_session()
    session = await anext(generator)
    return generator, session


async def test_exception_propagates_to_the_caller() -> None:
    """异常必须穿出去，不能被依赖「消化」成正常结束。"""
    generator, _ = await _open()

    with pytest.raises(RuntimeError, match="boom"):
        await generator.athrow(RuntimeError("boom"))


async def test_rollback_runs_before_the_exception_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    """回滚发生在异常离开依赖之前，而不是等到连接归还。

    用间谍确认调用，而不是查库：未提交的数据查不到，从数据侧看不出区别。
    """
    generator, session = await _open()
    calls: list[str] = []
    original = session.rollback  # type: ignore[attr-defined]

    async def _spy() -> None:
        calls.append("rollback")
        await original()

    monkeypatch.setattr(session, "rollback", _spy)

    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("boom"))

    assert calls == ["rollback"]


async def test_normal_exit_does_not_roll_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常结束不触发回滚分支。

    反过来错（把 `except` 写成 `finally`，或无条件 rollback）会让每个成功请求
    在退出时回滚一次，症状是「接口返回 200，刷新页面数据没了」。
    `GeneratorExit` 继承 `BaseException`，本来就不该被 `except Exception` 捕获。
    """
    generator, session = await _open()
    calls: list[str] = []

    async def _spy() -> None:
        calls.append("rollback")

    monkeypatch.setattr(session, "rollback", _spy)

    await generator.aclose()

    assert calls == []
