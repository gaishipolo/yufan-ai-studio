"""写路由的限流策略守卫。

这里守的不是「限流对不对」，而是「有没有人想过要不要限」。额度是产品决策，
会随时间变；「决定过」这件事才是可检查的。

和 `test_architecture.py` 的分工：那边管 import 方向与事务边界（静态结构），
这边管**运行期路由表**——只有把 app 真建起来才知道有哪些写接口，
静态扫装饰器会漏掉 `include_router`、`add_api_route` 和第三方挂载。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from api.deps import rate_limit
from api.ratelimit_policy import EXEMPT_WRITE_ROUTES, policy_violations
from core.config import settings


def _violations(app: FastAPI) -> tuple[list[str], list[str]]:
    return policy_violations(app.routes, api_prefix=settings.api_prefix)


def test_every_write_route_declares_rate_limit_policy(client: TestClient) -> None:
    """每条写路由要么挂了 `rate_limit`，要么在豁免表里写清理由。"""
    undeclared, stale = _violations(client.app)

    assert not undeclared, (
        "以下写路由既没挂 rate_limit，也不在 api/ratelimit_policy.py 的豁免表里：\n"
        + "\n".join(f"  - {item}" for item in undeclared)
        + "\n\n二选一：\n"
        "  1. 加依赖：`_: None = Depends(rate_limit(\"模块.动作\", limit=...))`\n"
        "  2. 写进 EXEMPT_WRITE_ROUTES，并说明它靠什么兜底（不接受「无」）"
    )
    assert not stale, (
        "豁免表里有失效条目（路由已改名或删除，表在提供过期信息）：\n"
        + "\n".join(f"  - {item}" for item in stale)
    )


def test_policy_covers_money_endpoints() -> None:
    """会真花钱的入口必须有 per-user 额度，不能只写一条豁免。

    这条用例钉的是策略本身：`filter` 出来的这些接口如果哪天被改成豁免，
    下面会红——比「某人 review 时想起来」可靠。
    """
    money = {
        "POST /assets/upload",
        "POST /assets/archive/upload",
        "POST /assets/archive/import",
        "POST /generations/image",
        "POST /images/{job_id}/retry",
        "POST /tasks",
        "POST /tasks/{task_id}/retry",
        "POST /templates/replacement-runs/{run_id}/execute",
    }
    assert not (money & set(EXEMPT_WRITE_ROUTES)), (
        "这些接口会写盘或提交供应商，不该走豁免：" + str(money & set(EXEMPT_WRITE_ROUTES))
    )


def test_exempt_reasons_are_real() -> None:
    """「无」「暂不」「以后再说」不算理由。

    豁免表唯一的产出是「下一个人的判断依据」。一句 `# 暂无限流` 会让表变成
    摆设——它看起来被审阅过，其实没有。
    """
    banned = ("暂无", "暂不", "以后再说", "todo", "tbd", "暂无限流")
    for key, reason in EXEMPT_WRITE_ROUTES.items():
        assert len(reason) >= 12, f"{key} 的理由太短：{reason!r}"
        assert not any(word in reason.lower() for word in banned), (
            f"{key} 的理由没有说明兜底手段：{reason!r}"
        )


def test_guard_sees_routes_behind_include_router() -> None:
    """守卫必须能看穿 `include_router` 的延迟包装。

    FastAPI 0.162 起 `app.include_router()` 不再把子路由摊平进 `app.routes`，
    而是放一层 `_IncludedRouter`。只看顶层就会得到「零条写路由」，
    守卫于是永远通过——**空检查比没有检查更危险**，它让人以为有人看着。
    """
    app = FastAPI()
    router = APIRouter(prefix="/demo")

    @router.post("/write")
    async def write() -> None: ...

    app.include_router(router, prefix="/api/v1")

    undeclared, _stale = policy_violations(app.routes, api_prefix="/api/v1")
    # 只断言「看得到」：stale 是拿真实 app 的豁免表比对的，这份合成应用里当然全都没有
    assert undeclared == ["POST /demo/write"]


def test_guard_accepts_declared_rate_limit() -> None:
    """挂了 `rate_limit` 的写路由不应被报出来（含嵌套依赖树里的那个）。"""
    app = FastAPI()
    router = APIRouter(prefix="/demo")

    @router.post("/write")
    async def write(
        _: None = Depends(rate_limit("demo.write", limit=10)),
    ) -> None: ...

    app.include_router(router, prefix="/api/v1")

    undeclared, _stale = policy_violations(app.routes, api_prefix="/api/v1")
    assert undeclared == []


def test_guard_treats_disabled_limit_as_declared() -> None:
    """`limit=0` 是「显式关闭」，不是「漏挂」——否则关掉额度的路由会被误报。"""
    app = FastAPI()
    router = APIRouter(prefix="/demo")

    @router.post("/write")
    async def write(
        _: None = Depends(rate_limit("demo.write", limit=0)),
    ) -> None: ...

    app.include_router(router, prefix="/api/v1")

    undeclared, _stale = policy_violations(app.routes, api_prefix="/api/v1")
    assert undeclared == []
