"""写路由的限流策略守卫。

这里守的不是「限流对不对」，而是「有没有人想过要不要限」。额度是产品决策，
会随时间变；「决定过」这件事才是可检查的。

第二层守的是「想过之后有没有人再看」：豁免条目带复核期限，理由里引用的文件与
权限码要真的存在。守卫核不了「这个接口现在值多少额度」（那要靠 `docs/OPS.md` §3
的写路由 P95 与供应商账单），能核的是「这句话还成不成立」和「多久没人看过了」。

和 `test_architecture.py` 的分工：那边管 import 方向与事务边界（静态结构），
这边管**运行期路由表**——只有把 app 真建起来才知道有哪些写接口，
静态扫装饰器会漏掉 `include_router`、`add_api_route` 和第三方挂载。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from api.deps import rate_limit
from api.ratelimit_policy import (
    EXEMPT_WRITE_ROUTES,
    REVIEW_HORIZON_DAYS,
    Exemption,
    policy_violations,
)
from core.config import settings
from core.permissions import catalog

API_ROOT = Path(__file__).resolve().parents[1]


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
    for key, exemption in EXEMPT_WRITE_ROUTES.items():
        assert len(exemption.why) >= 12, f"{key} 的理由太短：{exemption.why!r}"
        assert not any(word in exemption.why.lower() for word in banned), (
            f"{key} 的理由没有说明兜底手段：{exemption.why!r}"
        )


def test_exempt_reviews_are_current_and_honest() -> None:
    """复核期限：过期要红，写得太后也要红。

    这张表最大的风险不是写错，是**写完之后再没人看**：某个接口昨天只是改个名字，
    今天开始调供应商，理由里的「单行更新」变成了假话，而表还是绿的。
    守卫替代不了判断，能做的只是按时把人叫回来。

    `review_by` 上限是「今天 + REVIEW_HORIZON_DAYS」：写 2099 年不是判断，是拖延。
    """
    today = date.today()
    horizon = today + timedelta(days=REVIEW_HORIZON_DAYS)
    problems: list[str] = []
    for key, exemption in EXEMPT_WRITE_ROUTES.items():
        try:
            deadline = exemption.deadline()
        except ValueError:
            problems.append(f"{key}: review_by 不是 ISO 日期：{exemption.review_by!r}")
            continue
        if deadline <= today:
            problems.append(f"{key}: 复核期限已过（{deadline}），重新判断一次再改日期")
        elif deadline > horizon:
            problems.append(
                f"{key}: 复核期限 {deadline} 超过 {REVIEW_HORIZON_DAYS} 天上限"
                f"（最远 {horizon}）——这是拖延，不是判断"
            )
    assert not problems, "豁免表需要复核：\n" + "\n".join(f"  - {p}" for p in problems)


def test_exempt_reasons_cite_real_code(client: TestClient) -> None:
    """理由里引用的东西必须真的存在——这是「写对」里唯一能被自动核对的部分。

    判据只挑确定性的两类，不猜语义：

    - `` `xxx/y.py` ``：文件得在（`api/src/` 或仓库 `api/` 下）；
    - `` `资源:动作` ``：权限码得在权限目录里。

    文件被删、权限点被改名，那句话就成了谎言。这类腐烂 review 看不出来：
    它长得跟上一版一模一样。
    """
    # 权限目录在模块发现之后才有内容（`create_app()` 里注册），所以要拿 client
    # 这个夹具先把应用建起来——在模块导入期取会得到空集，从而「全部通过」
    codes = set(catalog.codes())
    assert codes, "权限目录是空的：这条守卫会变成永远通过的空检查"

    allowed_roots = (API_ROOT / "src", API_ROOT)
    problems: list[str] = []
    for key, exemption in EXEMPT_WRITE_ROUTES.items():
        for token in re.findall(r"`([\w\-/]+\.py)`", exemption.why):
            if not any((root / token).is_file() for root in allowed_roots):
                problems.append(f"{key}: 理由引用的文件不存在：{token}")
        for token in re.findall(r"`([a-z][a-z0-9_]*(?::[a-z0-9_]+)+)`", exemption.why):
            if token not in codes:
                problems.append(f"{key}: 理由引用的权限点不在目录里：{token}")
    assert not problems, (
        "豁免理由引用了不存在的东西（理由会慢慢变成假话）：\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_exemption_deadline_rejects_bad_date() -> None:
    """日期格式不对要在解析时炸，而不是被当成「永不过期」。"""
    with pytest.raises(ValueError):
        Exemption(why="随便写点什么凑够十二个字", review_by="2027/05/01").deadline()
    assert Exemption(why="随便写点什么凑够十二个字", review_by="2027-05-01").deadline()


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
