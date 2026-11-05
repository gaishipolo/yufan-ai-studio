"""凡是会算 scrypt 的路径，排队时都不能顺手占着数据库连接（R7-01 / R8-01 / R9-01）。

R6-02 把 scrypt 挪进线程池之后，事件循环不再被卡住，但「排队」这件事本身还在：
service 先查库拿口令哈希、再排队等线程池，而这次查询**已经把一条数据库连接取走了**，
session 要到请求结束才还回去。线程池只有 2~4 个位置，排队却是无界的——
几十个并发就能把连接池（默认 5+10=15）占满，所有要查库的接口跟着一起慢。

这条不变式有一个反复出现的失效模式：**每轮只给被点名的那条路径装闸门**。
R7-01 只装了 `authenticate`，R8-01 发现 `change_password` 漏了，R9-01 发现
`reset_password` 的闸门装在守卫之后。所以这里的用例覆盖全部会算 scrypt 的路径
（`authenticate` / `change_password` / `reset_password`；`create_user` 的建号路径
由 `test_identity_api.py` 的功能用例覆盖），以后新增第五条要一起补。

断言的不是耗时，而是**连接池的 checkpoint 峰值**：
耗时受机器快慢影响会飘，峰值不会——它只由闸门容量决定。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import pytest
from sqlalchemy import event

from core.context import CurrentUser
from core.exceptions import RateLimited
from core.security import _HASH_WORKERS
from modules.identity.errors import CredentialInvalid, OldPasswordMismatch

# 同时发起多少个请求：远大于连接池容量（15），修复前峰值会直接顶到池上限
CONCURRENCY = 40

# 闸门容量之外不允许多占：失败路径在闸门内部就 rollback 了，成功路径 commit 之后
# 连接也回池，所以峰值应当**正好**等于闸门容量。留余量只会让用例在实现退化时继续绿
# （R7-01 的初版就是这样：5 条连接里有 1 条是「已退闸门、还没 teardown」的）。
MAX_EXTRA = 0


@contextlib.contextmanager
def _connection_peak() -> Iterator[dict[str, int]]:
    """统计用例执行期间同时占用的连接数峰值。

    用 sqlalchemy 的 `checkout` / `checkin` 事件而不是「看耗时」：
    耗时受调度影响会飘，连接峰值是确定量，退化时一定看得出来。
    """
    from db.session import get_engine

    state = {"open": 0, "peak": 0}
    sync_engine = get_engine().sync_engine

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(dbapi_conn, record, proxy) -> None:  # noqa: ANN001
        state["open"] += 1
        state["peak"] = max(state["peak"], state["open"])

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_conn, record) -> None:  # noqa: ANN001
        state["open"] -= 1

    try:
        yield state
    finally:
        event.remove(sync_engine, "checkout", _on_checkout)
        event.remove(sync_engine, "checkin", _on_checkin)


def _make_gate_users(prefix: str, count: int, password: str) -> list[str]:
    """建 `count` 个账号，共用一个口令哈希（scrypt 只算一次），返回 id 列表。

    每个并发请求用**自己的账号**，是为了让限流键互不相同、也不会命中惩罚等待：
    这样「峰值 ≤ 闸门容量」只可能由闸门给出，不会被 `throttle.reject()` 顺手兜住（R9-04）。
    """
    from core.security import hash_password
    from db.session import get_session_factory
    from modules.identity.models import User

    encoded = hash_password(password)

    async def create() -> list[str]:
        async with get_session_factory()() as session:
            users = [
                User(
                    username=f"{prefix}-{index}",
                    password_hash=encoded,
                    display_name=f"{prefix}-{index}",
                    is_active=True,
                )
                for index in range(count)
            ]
            session.add_all(users)
            await session.commit()
            return [user.id for user in users]

    return asyncio.run(create())


def test_concurrent_logins_do_not_exhaust_the_connection_pool(client) -> None:  # noqa: ANN001
    """40 个并发登录（全部失败）期间，连接占用峰值不超过闸门容量。

    修复前实测峰值为 15（正好是 `5 + 10` 的池上限），修复后为 `_HASH_WORKERS`。
    失败路径是攻击者最容易走的路——匿名、不用猜对、每次都触发一次完整哈希。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    async def scenario() -> None:
        factory = get_session_factory()

        async def one(index: int) -> None:
            async with factory() as session:
                with pytest.raises(CredentialInvalid):
                    # 用户名与来源 IP 各不相同 → 限流键不同 → 没有惩罚等待，只有真实并发
                    await IdentityService(session).authenticate(
                        f"gate-user-{index}",
                        "wrong-pass",
                        client_ip=f"198.51.100.{index}",
                    )

        await asyncio.gather(*(one(i) for i in range(CONCURRENCY)))

    with _connection_peak() as state:
        asyncio.run(scenario())

    assert state["peak"] <= _HASH_WORKERS + MAX_EXTRA, (
        f"并发登录时同时占用 {state['peak']} 条连接（闸门容量 {_HASH_WORKERS}）："
        "闸门大概又被放到查库之后了——排队等哈希的请求不应持有连接"
    )


def test_change_password_does_not_exhaust_the_connection_pool(client) -> None:  # noqa: ANN001
    """改密路径是同一条不变式，不是只有登录才需要闸门（R8-01 / R9-04）。

    这条路由**只需要登录、不需要任何权限点**，旧口令传错时只花一次 scrypt、
    不改任何状态，可以无限重放——匿名爆破者做不到的事，一个只读账号 20 个并发
    就能做到：连接池顶满，所有要查库的接口一起慢。所以闸门必须在 `_require_user`
    **之前**，而且要覆盖「验旧口令 + 算新口令」两次哈希。

    这里刻意用**正确的旧口令 + 各自独立的账号**：旧版 20 个请求共用 `pw:u_001`
    且全部失败，第 5 个之后就被 `throttle.reject()` 挡在闸门外，绿灯里有一半是
    限流给的（R9-04）。一个断言最好只测一件事——闸门。
    """
    from db.session import get_session_factory
    from modules.identity.schema import PasswordChangeIn
    from modules.identity.service import IdentityService

    old_password = "old-pass-12345"
    user_ids = _make_gate_users("gate-pw", CONCURRENCY // 2, old_password)

    async def scenario() -> None:
        factory = get_session_factory()

        async def one(user_id: str) -> None:
            async with factory() as session:
                with contextlib.suppress(OldPasswordMismatch, RateLimited):
                    await IdentityService(session).change_password(
                        user_id,
                        PasswordChangeIn(
                            old_password=old_password, new_password="new-pass-12345"
                        ),
                    )

        await asyncio.gather(*(one(user_id) for user_id in user_ids))

    with _connection_peak() as state:
        asyncio.run(scenario())

    assert state["peak"] <= _HASH_WORKERS + MAX_EXTRA, (
        f"并发改密时同时占用 {state['peak']} 条连接（闸门容量 {_HASH_WORKERS}）："
        "闸门大概又只装在了 authenticate 上"
    )


def test_reset_password_does_not_exhaust_the_connection_pool(client) -> None:  # noqa: ANN001
    """管理员重置口令同样要「先排队、再借连接」（R9-01）。

    这条路径的门槛比改密高（要 `identity:update` 且目标不高于自己），所以它不是
    攻击者的首选；但**文档已经承诺了这条不变式**（`EXTENDING` §2.5 把
    `reset_password` 列为已包装的四条之一），守则写了、实现没守，比不写更糟——
    下一个人会照着文档以为它是安全的。

    原来的写法是 `_require_user` → `_guard_target_not_above` → 才进闸门，
    与紧挨着的 `create_user`（闸门打头）完全相反。实测 20 并发：
    连接峰值 15（池上限）、同期 `select 1` 296 ms。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    user_ids = _make_gate_users("gate-reset", CONCURRENCY // 2, "old-pass-12345")
    # 超管主体：否则守卫先拦掉，测不到闸门
    admin = CurrentUser(id="u_001", name="老大", permissions=frozenset({"*"}))

    async def scenario() -> None:
        factory = get_session_factory()

        async def one(user_id: str) -> None:
            async with factory() as session:
                # 每个请求重置自己的目标账号：没有限流、没有失败分支，只有真实并发
                await IdentityService(session).reset_password(
                    user_id, "new-pass-12345", admin
                )

        await asyncio.gather(*(one(user_id) for user_id in user_ids))

    with _connection_peak() as state:
        asyncio.run(scenario())

    assert state["peak"] <= _HASH_WORKERS + MAX_EXTRA, (
        f"并发重置口令时同时占用 {state['peak']} 条连接（闸门容量 {_HASH_WORKERS}）："
        "闸门大概又被放到了守卫之后——先借连接、再排队"
    )
