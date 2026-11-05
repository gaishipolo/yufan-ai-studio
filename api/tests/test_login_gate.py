"""登录排队时不能顺手占着数据库连接（R7-01）。

R6-02 把 scrypt 挪进线程池之后，事件循环不再被卡住，但「排队」这件事本身还在：
`authenticate` 先查库拿口令哈希、再排队等线程池，而这次查询**已经把一条数据库连接
取走了**，session 要到请求结束才还回去。线程池只有 2~4 个位置，排队却是无界的——
几十个匿名登录就能把连接池（默认 5+10=15）占满，所有要查库的接口跟着一起慢。

这条用例断言的不是耗时，而是**连接池的 checkpoint 峰值**：
耗时受机器快慢影响会飘，峰值不会——它只由闸门容量决定。
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from sqlalchemy import event

from core.exceptions import RateLimited
from core.security import _HASH_WORKERS
from modules.identity.errors import CredentialInvalid, OldPasswordMismatch

# 同时发起多少个登录：远大于连接池容量（15），修复前峰值会直接顶到池上限
CONCURRENCY = 40

# 闸门容量之外不允许多占：失败路径在闸门内部就提交了 rollback，连接当场回池，
# 所以峰值应当**正好**等于闸门容量。留 1 条余量只会让这条用例在实现退化时继续绿
# （R7-01 的初版就是这样：5 条连接里有 1 条是「已退闸门、还没 teardown」的）。
MAX_EXTRA = 0


def test_concurrent_logins_do_not_exhaust_the_connection_pool(client) -> None:  # noqa: ANN001
    """40 个并发登录（全部失败）期间，连接占用峰值不超过闸门容量 + 1。

    修复前实测峰值为 15（正好是 `5 + 10` 的池上限），修复后为 `_HASH_WORKERS + 1`。
    失败路径是攻击者最容易走的路——匿名、不用猜对、每次都触发一次完整哈希。
    """
    from db.session import get_engine, get_session_factory

    state = {"open": 0, "peak": 0}
    sync_engine = get_engine().sync_engine

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(dbapi_conn, record, proxy) -> None:  # noqa: ANN001
        state["open"] += 1
        state["peak"] = max(state["peak"], state["open"])

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_conn, record) -> None:  # noqa: ANN001
        state["open"] -= 1

    async def scenario() -> None:
        from modules.identity.service import IdentityService

        factory = get_session_factory()

        async def one(index: int) -> None:
            async with factory() as session:
                with pytest.raises(CredentialInvalid):
                    # 用户名与来源 IP 各不相同 → 限流键不同 → 没有惩罚等待，只有真实并发
                    await IdentityService(session).authenticate(
                        f"gate-user-{index}", "wrong-pass", client_ip=f"198.51.100.{index}"
                    )

        await asyncio.gather(*(one(i) for i in range(CONCURRENCY)))

    try:
        asyncio.run(scenario())
    finally:
        event.remove(sync_engine, "checkout", _on_checkout)
        event.remove(sync_engine, "checkin", _on_checkin)

    assert state["peak"] <= _HASH_WORKERS + MAX_EXTRA, (
        f"并发登录时同时占用 {state['peak']} 条连接（闸门容量 {_HASH_WORKERS}）："
        "闸门大概又被放到查库之后了——排队等哈希的请求不应持有连接"
    )


def test_change_password_does_not_exhaust_the_connection_pool(client) -> None:  # noqa: ANN001
    """改密路径是同一条不变式，不是只有登录才需要闸门（R8-01）。

    这条路由**只需要登录、不需要任何权限点**，旧口令传错时只花一次 scrypt、
    不改任何状态，可以无限重放——匿名爆破者做不到的事（R7-01 修好了），
    一个只读账号 20 个并发就能做到：连接池顶满，所有要查库的接口一起慢。

    所以闸门必须在 `_require_user` **之前**，而且要覆盖「验旧口令 + 算新口令」两次哈希。
    """
    from db.session import get_engine, get_session_factory

    state = {"open": 0, "peak": 0}
    sync_engine = get_engine().sync_engine

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(dbapi_conn, record, proxy) -> None:  # noqa: ANN001
        state["open"] += 1
        state["peak"] = max(state["peak"], state["open"])

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_conn, record) -> None:  # noqa: ANN001
        state["open"] -= 1

    async def scenario() -> None:
        from modules.identity.schema import PasswordChangeIn
        from modules.identity.service import IdentityService

        factory = get_session_factory()

        async def one() -> None:
            async with factory() as session:
                # 旧口令故意传错：这条路径「只花一次哈希、不改状态」，正是攻击者要的免费重放
                with contextlib.suppress(OldPasswordMismatch, RateLimited):
                    await IdentityService(session).change_password(
                        "u_001",  # seed 建的 admin
                        PasswordChangeIn(
                            old_password="wrong-pass", new_password="new-pass-123"
                        ),
                    )

        await asyncio.gather(*(one() for _ in range(20)))

    try:
        asyncio.run(scenario())
    finally:
        event.remove(sync_engine, "checkout", _on_checkout)
        event.remove(sync_engine, "checkin", _on_checkin)
        from modules.identity import throttle

        throttle.reset("pw:u_001")  # 失败计数不外溢到别的用例

    assert state["peak"] <= _HASH_WORKERS + MAX_EXTRA, (
        f"并发改密时同时占用 {state['peak']} 条连接（闸门容量 {_HASH_WORKERS}）："
        "闸门大概又只装在了 authenticate 上"
    )
