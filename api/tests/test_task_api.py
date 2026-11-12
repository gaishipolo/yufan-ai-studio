"""任务模块接口契约测试。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

TEST_PASSWORD = "test-pass-123"


def _provision(sub: str, name: str, permissions: tuple[str, ...]) -> None:
    """给测试主体造一个真实的「用户 + 专属角色 + 权限」组合。

    接入主体解析器之后，权限以数据库为准，自签令牌只会拿到 401，
    所以这里必须走真实数据；顺带让这些用例真的覆盖「角色 → 权限」展开。
    固定 id（`u_002` 之类）是为了让 `authorId` 断言有意义。
    """
    from core.security import hash_password
    from db.session import get_session_factory
    from modules.identity.models import Role, User
    from modules.identity.service import IdentityService

    async def run() -> None:
        async with get_session_factory()() as session:
            service = IdentityService(session)
            code = f"test-{sub}"
            role = await service.roles.get_by_code(code)
            if role is None:
                role = Role(code=code, name=code, is_builtin=False, sort_order=999)
                session.add(role)
                await session.flush()
            await service.roles.set_permissions(role.id, list(permissions))

            user = await service.users.get(sub)
            if user is None:
                user = User(
                    id=sub,
                    username=sub,
                    password_hash=hash_password(TEST_PASSWORD),
                    display_name=name or sub,
                    is_active=True,
                )
                session.add(user)
                await session.flush()
            else:  # 同名主体被多个用例复用时复位状态
                user.display_name = name or sub
                user.is_active = True
                user.password_hash = hash_password(TEST_PASSWORD)
            await service.users.set_roles(user.id, [role.id])
            await session.commit()

    asyncio.run(run())


def _bearer(
    client: TestClient,
    sub: str,
    name: str = "",
    permissions: tuple[str, ...] | list[str] = ("*",),
) -> dict[str, str]:
    """造账号并登录，返回 Authorization 头。"""
    _provision(sub, name, tuple(permissions))
    resp = client.post(
        "/api/v1/identity/login",
        json={"username": sub, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _create(client: TestClient, api: str, **overrides) -> dict:
    payload = {
        "prompt": "测试用画面描述：雪山日出",
        "model": "Seedance 2.5",
        "durationSec": 12,
        "aspectRatio": "9:16",
    } | overrides
    resp = client.post(f"{api}/tasks", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_list_shape_and_stats(client: TestClient, api: str) -> None:
    data = client.get(f"{api}/tasks", params={"pageSize": 5}).json()["data"]
    assert data["pageSize"] == 5
    assert isinstance(data["items"], list)
    stats = data["stats"]
    assert stats["all"] == sum(stats[k] for k in ("queued", "running", "completed", "failed"))
    assert {"id", "status", "model", "durationSec", "aspectRatio", "createdAt"} <= set(
        data["items"][0]
    )


def test_create_and_detail(client: TestClient, api: str) -> None:
    task = _create(client, api, title="自定义标题")
    assert task["status"] == "queued"
    assert task["title"] == "自定义标题"
    assert task["progress"] == 0

    detail = client.get(f"{api}/tasks/{task['id']}").json()["data"]
    assert detail["id"] == task["id"]


def test_create_without_title_uses_prompt(client: TestClient, api: str) -> None:
    task = _create(client, api, prompt="没有标题的任务")
    assert task["title"].startswith("没有标题的任务")


def test_validation_error_shape(client: TestClient, api: str) -> None:
    resp = client.post(f"{api}/tasks", json={"prompt": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 40001
    assert body["details"][0]["field"] == "prompt"


def test_not_found(client: TestClient, api: str) -> None:
    resp = client.get(f"{api}/tasks/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40400


def test_filter_and_search(client: TestClient, api: str) -> None:
    running = client.get(f"{api}/tasks", params={"status": "running"}).json()["data"]
    assert all(item["status"] == "running" for item in running["items"])

    found = client.get(f"{api}/tasks", params={"keyword": "冰川"}).json()["data"]
    assert found["total"] >= 1


def test_sort(client: TestClient, api: str) -> None:
    asc = client.get(f"{api}/tasks", params={"sort": "createdAt_asc", "pageSize": 3}).json()["data"]
    dates = [item["createdAt"] for item in asc["items"]]
    assert dates == sorted(dates)


def test_favorite_toggle(client: TestClient, api: str) -> None:
    task = _create(client, api)
    first = client.post(f"{api}/tasks/{task['id']}/favorite").json()["data"]["favorited"]
    second = client.post(f"{api}/tasks/{task['id']}/favorite").json()["data"]["favorited"]
    assert first != second


def test_retry_only_failed(client: TestClient, api: str) -> None:
    task = _create(client, api)
    resp = client.post(f"{api}/tasks/{task['id']}/retry")
    assert resp.status_code == 409
    assert resp.json()["code"] // 100 == 409


def test_batch_delete(client: TestClient, api: str) -> None:
    a = _create(client, api)
    b = _create(client, api)
    body = client.request("DELETE", f"{api}/tasks", json={"ids": [a["id"], b["id"]]}).json()
    assert body["data"]["deleted"] == 2


def test_scope_mine_isolates_authors(client: TestClient, api: str) -> None:
    """回归：`scope=mine` 必须只返回本人的任务。

    曾经的写法是 `query.scope is TaskScope.MINE`，而当时 `CamelModel` 开着
    `use_enum_values=True`，显式传入的枚举会被转成 str，身份判断恒为 False，
    导致作者过滤完全失效（跨用户看到全站任务）。该配置已删，但这条回归用例
    保留：归一逻辑（`TaskScope(query.scope)`）不该被「现在都是枚举了」说服掉。
    """
    bob = _bearer(client, "u_030", "bob")

    created = client.post(
        f"{api}/tasks",
        json={"prompt": "bob 的私有任务", "model": "Seedance 2.5"},
        headers=bob,
    )
    assert created.status_code == 201, created.text

    mine = client.get(f"{api}/tasks", params={"scope": "mine"}, headers=bob).json()["data"]
    assert mine["total"] >= 1
    assert {item["authorId"] for item in mine["items"]} == {"u_030"}
    assert mine["stats"]["all"] == mine["total"]


def test_scope_all_degrades_without_cross_user_permission(client: TestClient, api: str) -> None:
    """`scope=all` 不是「谁都能看全站」：缺 `task:read:all` 时按本人过滤。

    旧实现里 ALL 分支什么都不加，等价于对全部用户开放；同一个用户列表能看到
    别人的任务，点进详情却 403，这种不一致最难排查。
    """
    reader = _bearer(client, "u_010", "reader", permissions=["task:read", "task:create"])
    assert (
        client.post(f"{api}/tasks", json={"prompt": "reader 自己的"}, headers=reader).status_code
        == 201
    )

    mine = client.get(f"{api}/tasks", params={"scope": "mine"}, headers=reader).json()["data"]
    everyone = client.get(f"{api}/tasks", params={"scope": "all"}, headers=reader).json()["data"]

    assert everyone["total"] == mine["total"]
    assert {item["authorId"] for item in everyone["items"]} == {"u_010"}


def test_scope_all_with_permission_sees_others(client: TestClient, api: str) -> None:
    """带 `task:read:all` 的运营角色，才真的能看到别人的任务。"""
    ops = _bearer(client, "u_011", "ops", permissions=["task:read", "task:read:all"])
    everyone = client.get(f"{api}/tasks", params={"scope": "all"}, headers=ops).json()["data"]
    assert "u_001" in {item["authorId"] for item in everyone["items"]}


def test_detail_and_delete_use_the_same_permission(client: TestClient, api: str) -> None:
    """跨用户可见性只有一个开关：列表、详情、删除必须一致。"""
    owner_task = _create(client, api, title="属于 u_001 的任务")
    reader = _bearer(client, "u_012", "reader2", permissions=["task:read", "task:delete"])
    ops = _bearer(client, "u_013", "ops2", permissions=["task:read", "task:read:all"])

    assert client.get(f"{api}/tasks/{owner_task['id']}", headers=reader).status_code == 403
    assert client.get(f"{api}/tasks/{owner_task['id']}", headers=ops).status_code == 200

    # 没有跨用户权限时，删除别人的任务等于「没删掉」，不会真的动手
    body = client.request(
        "DELETE", f"{api}/tasks", json={"ids": [owner_task["id"]]}, headers=reader
    ).json()
    assert body["data"]["deleted"] == 0
    # 必须回报「跳过了哪条」：只回 deleted=0 的话，前端分不清
    # 「本来就不存在」和「没权限」，用户会以为自己少选了（R4-05）
    assert body["data"]["skipped"] == [owner_task["id"]]
    assert client.get(f"{api}/tasks/{owner_task['id']}").status_code == 200


def test_scope_mine_invisible_to_other_user(client: TestClient, api: str) -> None:
    """反向验证：别人的 scope=mine 里不能出现 bob 的任务。"""
    bob = _bearer(client, "u_030", "bob")
    alice = _bearer(client, "u_031", "alice")

    client.post(f"{api}/tasks", json={"prompt": "bob 的另一条"}, headers=bob)
    seen = client.get(f"{api}/tasks", params={"scope": "mine"}, headers=alice).json()["data"]
    assert seen["total"] == 0
    assert all(item["authorId"] == "u_031" for item in seen["items"])


def test_dispatch_failure_marks_task_failed(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队失败不能让任务停在 queued：必须显式失败并可重试。"""
    from modules.task import service as task_service

    class _BoomRunner:
        async def dispatch(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("队列不可用")

    monkeypatch.setattr(task_service, "get_runner", lambda queue: _BoomRunner())

    marker = "入队失败回归用例"
    resp = client.post(f"{api}/tasks", json={"prompt": marker})
    assert resp.status_code == 502
    assert resp.json()["code"] == 50200

    monkeypatch.undo()
    found = client.get(f"{api}/tasks", params={"keyword": marker}).json()["data"]
    assert found["total"] == 1
    task = found["items"][0]
    assert task["status"] == "failed"
    assert "调度失败" in task["errorMessage"]


def test_empty_title_falls_back_to_placeholder(client: TestClient, api: str) -> None:
    """PATCH 传空标题是合法意图（清空），不能被静默忽略。"""
    task = _create(client, api, title="先有标题")
    patched = client.patch(f"{api}/tasks/{task['id']}", json={"title": "   "}).json()["data"]
    assert patched["title"] == "未命名任务"


# --------------------------------------------------------------------------- #
# 创建入口的模型目录校验（错误必须在同步接口暴露，而不是等 worker 里失败）
# --------------------------------------------------------------------------- #
def test_create_rejects_unknown_model(client: TestClient, api: str) -> None:
    resp = client.post(f"{api}/tasks", json={"prompt": "未知模型", "model": "不存在的模型"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40000
    assert "可用模型" in body["message"]
    assert body["details"]["availableModels"]


def test_create_rejects_duration_out_of_range(client: TestClient, api: str) -> None:
    """目录里 seedance 的上限是 12s，30s 必须当场被挡住。"""
    resp = client.post(
        f"{api}/tasks",
        json={"prompt": "超时长", "model": "seedance-2.5", "durationSec": 30},
    )
    assert resp.status_code == 400
    assert resp.json()["details"]["field"] == "durationSec"


def test_create_rejects_wrong_capability(client: TestClient, api: str) -> None:
    """图片模型不能拿来建视频任务。"""
    resp = client.post(f"{api}/tasks", json={"prompt": "用错模型", "model": "seedream-3"})
    assert resp.status_code == 400
    assert "能力" in resp.json()["message"]


def test_create_defaults_come_from_catalog(client: TestClient, api: str) -> None:
    """不传模型/时长时取目录默认值，而不是代码里写死的 15s（超上限）。

    默认值必须是**公开且真实**的模型：占位模型（echo 的 `seedance-2.5`）
    实现是假的，`internal` 模型（`libtv-wan3-replacement`）露脸必然 400 ——
    两者当默认都会让用户「不传 model」然后拿到跑不通的结果。
    """
    task = _create(client, api, prompt="默认值任务", model=None, durationSec=None)
    assert task["modelId"] == "agnes-video-2.5-flash"
    assert task["durationSec"] == 5

    # 默认值必须落在用户能选到的公开模型上
    catalog = client.get(f"{api}/models", params={"capability": "video"}).json()["data"]
    public_ids = {item["id"] for item in catalog["items"]}
    assert task["modelId"] in public_ids
    defaults = [item["id"] for item in catalog["items"] if item["isDefault"]]
    assert defaults == [task["modelId"]]


def test_create_normalizes_model_id_and_label(client: TestClient, api: str) -> None:
    """入参接受 id 或 label，出参与库里都归一成「id + 可读名」成对出现。"""
    by_id = _create(client, api, prompt="按 id 提交", model="kling-2.1")
    assert by_id["modelId"] == "kling-2.1"
    assert by_id["model"] == "可灵 2.1"

    by_label = _create(client, api, prompt="按名称提交", model="可灵 2.1")
    assert by_label["modelId"] == "kling-2.1"


def test_create_echoes_style_and_reference_assets(client: TestClient, api: str) -> None:
    """契约里声明的字段必须真的落库并回显，不能「收了就丢」。"""
    task = _create(
        client,
        api,
        prompt="带参考图",
        referenceAssetIds=["a1", "a2"],
        stylePreset="电影感",
    )
    assert task["referenceAssetIds"] == ["a1", "a2"]
    assert task["stylePreset"] == "电影感"

    detail = client.get(f"{api}/tasks/{task['id']}").json()["data"]
    assert detail["referenceAssetIds"] == ["a1", "a2"]
    assert detail["stylePreset"] == "电影感"



# --------------------------------------------------------------------------- #
# 重试 × 提交状态机（R3-01 回归）
# --------------------------------------------------------------------------- #
def test_retry_after_real_failure_resubmits(client: TestClient, api: str) -> None:
    """提交成功之后的自然失败（轮询超时 / 供应商报错）→ 重试必须真的重新提交。

    `retry_task` 清了 `external_id`，若不同时复位 `submit_attempts`，
    记录会命中 `submit_unknown`（attempts>0 且无 external_id），
    被防重复计费的防线误杀成「提交结果未知」，重试按钮变成摆设。
    """
    import asyncio

    from db.session import get_session_factory
    from generation.runner import JOB_SUBMIT, GenerationRunner
    from infra.queue.base import Job
    from infra.queue.memory import MemoryJobQueue
    from modules.task.enums import TaskStatus
    from modules.task.models import VideoTask

    async def scenario() -> tuple[VideoTask, list[dict]]:
        factory = get_session_factory()
        async with factory() as session:
            task = VideoTask(
                title="提交成功后失败的",
                prompt="雨夜霓虹",
                status=TaskStatus.FAILED.value,
                model="Seedance 2.5",
                model_id="seedance-2.5",
                external_id="echo-video-abcd-1700000000",
                submit_attempts=1,  # 提交成功后残留的意图标记
                error_message="生成超时，请重试",
                author_id="u_001",
                author_name="老大",
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        # 用户点「重试」
        resp = client.post(f"{api}/tasks/{task_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "queued"

        # worker 消费这条 submit job（echo 供应商会立即受理）
        queue = MemoryJobQueue()
        await GenerationRunner(queue).handle_submit(
            Job(name=JOB_SUBMIT, payload={"kind": "video", "record_id": task_id})
        )

        async with factory() as session:
            after = await session.get(VideoTask, task_id)
            assert after is not None
            return after, queue.snapshot()

    after, jobs = asyncio.run(scenario())

    assert after.status == TaskStatus.RUNNING.value, after.error_message
    assert after.external_id
    assert after.error_message is None
    assert any(job["key"] == f"poll:video:{after.id}" for job in jobs)


# --------------------------------------------------------------------------- #
# canReadAll 透传 + stats 口径（R3-03）
# --------------------------------------------------------------------------- #
def test_list_reports_can_read_all_flag(client: TestClient, api: str) -> None:
    """前端要靠 canReadAll 决定是否展示「全部」范围，后端必须如实透传。

    降级是静默的（不 403），如果连「我被降级了」都不告诉前端，
    「全部任务」下拉就是在说谎。
    """
    admin_list = client.get(f"{api}/tasks").json()["data"]
    assert admin_list["canReadAll"] is True

    reader = _bearer(client, "u_020", "reader3", permissions=["task:read", "task:create"])
    client.post(f"{api}/tasks", json={"prompt": "reader3 自己的"}, headers=reader)
    reader_list = client.get(f"{api}/tasks", headers=reader).json()["data"]
    assert reader_list["canReadAll"] is False


def test_stats_scope_follows_request_and_degrades(client: TestClient, api: str) -> None:
    """`/tasks/stats` 接受 scope，与列表共用同一套降级规则。"""
    # 管理员：默认 mine 只算自己的，scope=all 才是全站
    mine_stats = client.get(f"{api}/tasks/stats").json()["data"]
    all_stats = client.get(f"{api}/tasks/stats", params={"scope": "all"}).json()["data"]
    assert all_stats["all"] >= mine_stats["all"]

    # 无 read:all 的账号：scope=all 静默降级为本人，不报错也不多算
    reader = _bearer(client, "u_021", "reader4", permissions=["task:read", "task:create"])
    client.post(f"{api}/tasks", json={"prompt": "reader4 的任务"}, headers=reader)

    degraded = client.get(
        f"{api}/tasks/stats", params={"scope": "all"}, headers=reader
    ).json()["data"]
    own = client.get(f"{api}/tasks/stats", params={"scope": "mine"}, headers=reader).json()["data"]

    assert degraded["all"] == own["all"]
    assert degraded["all"] >= 1
    assert all_stats["all"] > degraded["all"], "全站总数必须大于单个用户的可见数"


def test_retry_others_task_needs_its_own_permission(
    client: TestClient, api: str
) -> None:
    """`task:read:all` 只解决「看得见」，替别人重试是再花一次钱（R4-06）。

    这条边界靠一个独立权限点表达，而不是让「能看全部」顺带解锁计费动作。
    """
    from db.session import get_session_factory
    from modules.task.enums import TaskStatus
    from modules.task.models import VideoTask

    async def _make_failed_task() -> str:
        async with get_session_factory()() as session:
            task = VideoTask(
                title="别人的失败任务",
                prompt="沙漠公路",
                status=TaskStatus.FAILED.value,
                model="Seedance 2.5",
                model_id="seedance-2.5",
                error_message="生成超时，请重试",
                author_id="u_001",
                author_name="老大",
            )
            session.add(task)
            await session.commit()
            return task.id

    task_id = asyncio.run(_make_failed_task())

    reader = _bearer(
        client,
        "u_040",
        "ops_reader",
        permissions=["task:read", "task:read:all", "task:update"],
    )
    denied = client.post(f"{api}/tasks/{task_id}/retry", headers=reader)
    assert denied.status_code == 403
    assert "重试他人" in denied.json()["message"]

    allowed = _bearer(
        client,
        "u_041",
        "ops_retry",
        permissions=["task:read", "task:read:all", "task:update", "task:retry:any"],
    )
    assert client.post(f"{api}/tasks/{task_id}/retry", headers=allowed).status_code == 200

    # 关键：**只有** retry:any、没有 read:all 的人也必须能重试（R5-05）。
    # 「能重试但看不见列表」是合理组合（客服按链接代跑），
    # 如果先过可见性检查，这个权限点就是「勾了没反应」。
    task_id_2 = asyncio.run(_make_failed_task())
    responder = _bearer(
        client,
        "u_042",
        "ops_responder",
        permissions=["task:read", "task:update", "task:retry:any"],
    )
    assert client.post(f"{api}/tasks/{task_id_2}/retry", headers=responder).status_code == 200


def test_retry_returns_only_the_action_result(client: TestClient, api: str) -> None:
    """`retry` 只返回动作结果，不返回整份任务（R6-05）。

    `task:retry:any` 与 `task:read:all` 是两个开关：客服只需要「帮别人重跑」，
    不该顺带获得「按 id 读任意失败任务的 prompt 与作者」这条读通道。
    """
    from db.session import get_session_factory
    from modules.task.enums import TaskStatus
    from modules.task.models import VideoTask

    async def _make_failed_task() -> str:
        async with get_session_factory()() as session:
            task = VideoTask(
                title="机密项目A",
                prompt="客户的保密画面描述",
                status=TaskStatus.FAILED.value,
                model="Seedance 2.5",
                model_id="seedance-2.5",
                error_message="生成超时，请重试",
                author_id="u_001",
                author_name="老大",
            )
            session.add(task)
            await session.commit()
            return task.id

    task_id = asyncio.run(_make_failed_task())
    responder = _bearer(
        client,
        "u_043",
        "ops_link",
        permissions=["task:read", "task:update", "task:retry:any"],
    )

    resp = client.post(f"{api}/tasks/{task_id}/retry", headers=responder)
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert set(data) == {"id", "status", "updatedAt"}
    assert data["id"] == task_id
    assert data["status"] == "queued"
    # 「看不见」和「拿不到」必须是同一件事
    assert client.get(f"{api}/tasks/{task_id}", headers=responder).status_code == 403
