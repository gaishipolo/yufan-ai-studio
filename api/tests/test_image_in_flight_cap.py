"""单账号「在途生图上限」：一个人攒几十条会把上游排队位次挤掉。

为什么要这道闸门：`/images` 只有**频率**限流（`image.generate` 每分钟 60 次），
频率限流拦得住「手抖连点」，拦不住「一分钟点 60 次」。而每条生图都要占供应商
账号的上游排队位次（**账号是全团队共享的**）——挂 60 条的人把别人的生图排到
自己后面，自己也一样在队尾，界面上每一条还都显示「已提交」。

判据与视频任务同一口径：**queued + running 才算在途**，completed / failed
立刻释放名额。

注意这里压的**不是** worker 槽位：等出图不占槽位，槽位只在提交 / 下载那几秒
被占。所以「调大并发」解决不了这件事——这也是为什么这条闸门不能省。

用例不假设库里干净：先探出当前在途数，再按「基线 + N」设上限。这样它跟同一
session 里别的用例造出来的记录互不干扰。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from db.session import get_session_factory
from modules.image.models import ImageJob

API = "/api/v1"


# --------------------------------------------------------------- 直连数据库的小工具


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        f"{API}/identity/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _owner_id(client: TestClient, headers: dict[str, str]) -> str:
    """当前登录账号的 id —— 上限是**每账号**的，得用真实 id，不能写死。"""
    resp = client.get(f"{API}/identity/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["id"])


def _in_flight(owner_id: str) -> int:
    """走服务里同一条判据，不另写一份 SQL —— 否则这条用例守的是我抄的那份。"""

    async def run() -> int:
        from sqlalchemy import func, select

        from generation.spec import STATUS_QUEUED, STATUS_RUNNING

        async with get_session_factory()() as session:
            stmt = select(func.count()).select_from(ImageJob).where(
                ImageJob.owner_id == owner_id,
                ImageJob.status.in_((STATUS_QUEUED, STATUS_RUNNING)),
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    return asyncio.run(run())


def _create_job(owner_id: str, status: str = "queued") -> str:
    """直接落一条生图记录（绕开供应商，省得依赖模型配置）。"""

    async def run() -> str:
        async with get_session_factory()() as session:
            job = ImageJob(
                name="在途占位",
                prompt="压测用的占位提示词",
                owner_id=owner_id,
                status=status,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id

    return asyncio.run(run())


def _set_status(job_id: str, status: str) -> None:
    async def run() -> None:
        async with get_session_factory()() as session:
            row = await session.get(ImageJob, job_id)
            assert row is not None, f"生图记录不存在：{job_id}"
            row.status = status
            await session.commit()

    asyncio.run(run())


def _cap(monkeypatch: Any, value: int) -> None:
    """改的是 `get_settings()` 那个单例上的字段：服务里每次调用都现读。"""
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "image_max_in_flight_per_user", value)


def _submit(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        f"{API}/generations/image",
        json={"prompt": "一条普通的生图请求"},
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def test_达到上限后多出来的一条被拒(
    client: TestClient, monkeypatch: Any
) -> None:
    """在途达到上限时，第 N+1 条被拒（429），并且说清「上限」与「当前在途数」。

    消息里必须带这两个数：只说「已达上限」，用户不知道自己差几条、
    也不知道是哪个上限，只能反复试。
    """
    headers = _login(client, "admin", "admin@12345")
    owner = _owner_id(client, headers)

    _cap(monkeypatch, 0)  # 先不限：探出这个账号已经积了多少条
    baseline = _in_flight(owner)
    limit = baseline + 2
    _cap(monkeypatch, limit)

    for _ in range(limit - baseline):
        _create_job(owner)

    response = _submit(client, headers)
    assert response.status_code == 429, response.text

    message = response.json()["message"]
    assert f"上限 {limit} 条" in message, f"消息里没有上限：{message}"
    assert f"{limit} 条生图在排队或生成中" in message, f"消息里没有在途数：{message}"


def test_上限为0表示不限(client: TestClient, monkeypatch: Any) -> None:
    """上限 0 = 不限（与视频任务同一约定）。

    部署方有时就是要放开（比如内网自助研究），得有个明确的「关掉」开关，
    而不是靠把上限调成一个很大的数。
    """
    headers = _login(client, "admin", "admin@12345")
    owner = _owner_id(client, headers)

    for _ in range(3):
        _create_job(owner)
    _cap(monkeypatch, 0)

    response = _submit(client, headers)
    assert response.status_code != 429, response.text


def test_一条跑完就能再提交(client: TestClient, monkeypatch: Any) -> None:
    """名额随终态释放 —— 否则「上限」会变成「这辈子只能提交 N 条」。

    这是这道闸门最容易出的错：判据里如果把 completed 也算进去，
    用户一天就只能生 N 张图，而界面上的提示是「在途上限」。
    """
    headers = _login(client, "admin", "admin@12345")
    owner = _owner_id(client, headers)

    _cap(monkeypatch, 0)
    baseline = _in_flight(owner)
    probe_id = _create_job(owner)

    _cap(monkeypatch, baseline + 1)
    blocked = _submit(client, headers)
    assert blocked.status_code == 429, blocked.text

    _set_status(probe_id, "completed")
    assert _in_flight(owner) == baseline, "完成的记录还占着名额"

    allowed = _submit(client, headers)
    assert allowed.status_code != 429, allowed.text


def test_别人的在途不影响我(client: TestClient, monkeypatch: Any) -> None:
    """别人的在途不算我的——上限是**每账号**的，不是全局的。"""
    headers = _login(client, "admin", "admin@12345")
    owner = _owner_id(client, headers)

    # 另一个账号的在途
    _create_job("u_999")

    _cap(monkeypatch, 0)
    baseline = _in_flight(owner)
    _cap(monkeypatch, baseline + 1)

    response = _submit(client, headers)
    assert response.status_code != 429, "别人的在途把这条挡住了——上限是每账号的"


def test_默认上限是开着的() -> None:
    """默认配置不能被改成 0——**0 的含义是「不限」**。

    一旦有人把默认值改成 0，这条闸门就在所有部署里静默失效了，而且
    没有任何症状：生图照常提交，只是把共享上游的排队位次又占满了。
    """
    from core.config import Settings

    # 读**字段默认值**，不是实例化后的值：实例化会读环境变量
    # （测试环境里这个变量被刻意设成 0），那不是「默认」。
    default = Settings.model_fields["image_max_in_flight_per_user"].default
    assert default > 0, f"默认上限是 {default}（= 不限），闸门默认关着"
    # 也不该是「离谱地大」——那等于没设
    assert default <= 20, f"默认上限 {default} 大得没有约束力"


def test_闸门接在提交路径上() -> None:
    """`generate()` 里必须调用在途检查——删掉它没有任何症状。

    删掉这一行，生图功能照常工作、测试照常通过（只要上限调大），
    只是共享上游又被占满了。所以这里用源码扫描把它钉住。
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src" / "modules" / "image" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    generate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate"
    )
    called = {
        node.func.attr
        for node in ast.walk(generate)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_assert_in_flight_room" in called, (
        "generate() 里没有在途检查——闸门形同虚设"
    )
