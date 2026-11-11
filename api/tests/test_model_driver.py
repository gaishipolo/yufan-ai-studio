"""模型驱动模块：开发标准校验、目录自动载入、注入口与统一生成门面。

钉住四条最容易悄悄退化的性质：
1. 驱动不进 `.env` 也能被发现和路由；
2. 注入口存在且 fail-closed；
3. 走注入口 / REST 提交都落 `driver_jobs`，并接上 `generation.runner` 编排；
4. 失败记录有人工自救出口，且不会绕过「可能已计费」确认。
"""

from __future__ import annotations

import argparse
import asyncio

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from core.exceptions import BadRequest, Unavailable
from core.ports.model_generation import (
    ModelGeneration,
    SubmitSpec,
    get_model_generation,
    has_model_generation,
    register_model_generation,
    reset_model_generation,
)
from db.session import get_session_factory
from generation.pipeline import get_pipeline
from infra import get_model_router
from modules.model_driver.cli import cmd_check, cmd_list
from modules.model_driver.models import KIND_MODEL_DRIVER, STATUS_CANCELLED, DriverJob
from modules.model_driver.registry import (
    driver_summaries,
    ensure_drivers_registered,
    load_errors,
    reset_drivers,
)
from modules.model_driver.service import ModelGenerationFacade
from modules.model_driver.spec import validate_driver

API = "/api/v1"


async def _insert(job: DriverJob) -> str:
    async with get_session_factory()() as session:
        session.add(job)
        await session.flush()
        job_id = job.id
        await session.commit()
        return job_id


async def _drop(job_id: str) -> None:
    async with get_session_factory()() as session:
        row = await session.get(DriverJob, job_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


def _cleanup(job_ids: list[str]) -> None:
    for job_id in job_ids:
        asyncio.run(_drop(job_id))


def _me(client: TestClient) -> str:
    resp = client.get(f"{API}/identity/me")
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


# --------------------------------------------------------------------- 自动载入
def test_drivers_are_auto_loaded_without_env_listing(client: TestClient) -> None:
    ensure_drivers_registered()
    infos = {item.name: item for item in driver_summaries()}
    assert {"md_async", "md_sync"} <= set(infos)
    assert all(item.loaded for item in infos.values()), load_errors()
    assert not load_errors()
    # 关键：这两个名字不在配置清单里，是扫描 drivers/ 得来的
    assert "md_async" not in settings.ai_provider_list
    assert "md_sync" not in settings.ai_provider_list
    router = get_model_router()
    assert {"md_async", "md_sync"} <= set(router.provider_names())
    assert router.resolve("md-sync-image") is not None
    assert router.provider_for("md-async-video").name == "md_async"


def test_contract_validation_rejects_broken_drivers() -> None:
    from modules.model_driver.drivers.mock_async import MockAsyncDriver
    from modules.model_driver.drivers.mock_sync import MockSyncDriver

    assert validate_driver(MockAsyncDriver()) == []
    assert validate_driver(MockSyncDriver()) == []

    class Broken:
        name = "9-bad name"
        capabilities = frozenset()
        models = ()

    problems = validate_driver(Broken())
    assert any("name" in item for item in problems)
    assert any("capabilities" in item for item in problems)
    assert any("models" in item for item in problems)
    assert any("submit" in item for item in problems)

    class WrongOwner:
        name = "wrong"
        capabilities = frozenset({"image"})
        models = (object(),)

    assert validate_driver(WrongOwner())


def test_reset_drivers_self_heals(client: TestClient) -> None:
    """路由单例没换、只清发现结果时，不能把自己的驱动判成重名冲突。"""
    reset_drivers()
    ensure_drivers_registered()
    assert not load_errors()
    assert all(item.loaded for item in driver_summaries())


# ----------------------------------------------------------------------- 注入口
def test_port_is_registered_and_fail_closed(client: TestClient) -> None:
    assert has_model_generation()
    facade = get_model_generation()
    assert isinstance(facade, ModelGenerationFacade)
    assert isinstance(facade, ModelGeneration)

    reset_model_generation()
    try:
        assert not has_model_generation()
        with pytest.raises(Unavailable):
            get_model_generation()
    finally:
        register_model_generation(ModelGenerationFacade())
    assert has_model_generation()


def test_pipeline_exposes_recovery_hook(client: TestClient) -> None:
    pipeline = get_pipeline(KIND_MODEL_DRIVER)
    assert hasattr(pipeline, "list_unfinished")


# ------------------------------------------------------------------- 门面 / REST
def test_submit_query_and_cancel_via_rest(client: TestClient) -> None:
    resp = client.post(
        f"{API}/model-driver/tasks",
        json={"model": "md-sync-image", "prompt": "冒烟", "source": "pytest"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    job_id = data["id"]
    try:
        assert data["status"] == "queued"
        assert data["capability"] == "image"
        assert data["source"] == "pytest"

        got = client.get(f"{API}/model-driver/tasks/{job_id}")
        assert got.status_code == 200
        assert got.json()["data"]["id"] == job_id

        cancelled = client.post(f"{API}/model-driver/tasks/{job_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["data"]["status"] == STATUS_CANCELLED
        assert client.post(f"{API}/model-driver/tasks/{job_id}/cancel").status_code == 409
    finally:
        _cleanup([job_id])


def test_port_direct_submit(client: TestClient) -> None:
    """下游模块不经 HTTP、直接调注入口也必须能落库并入队。"""
    ticket = asyncio.run(
        get_model_generation().submit(
            SubmitSpec(
                model="md-async-video",
                prompt="端口直调",
                owner_id=_me(client),
                source="port-test",
                params={"durationSec": 5},
            )
        )
    )
    try:
        assert ticket.task_id
        assert ticket.capability == "video"
        assert ticket.status == "queued"
        assert client.get(f"{API}/model-driver/tasks/{ticket.task_id}").status_code == 200
    finally:
        _cleanup([ticket.task_id])


def test_reference_urls_survive_to_build_request(client: TestClient) -> None:
    """参考图必须原样活到 `build_request`——图生图/多图合成全靠这条链。

    链路是 REST/端口 → `encode_request(params, reference_urls)` 落库 →
    `_to_record` 解回 → `build_request` 交给驱动。中间任何一环把它丢了，
    表现都是「提示词说改这张图，供应商却当成文生图重画一张」，而且不报错。
    """
    refs = ["https://example.com/char-1.png", "https://example.com/char-2.png"]
    resp = client.post(
        f"{API}/model-driver/tasks",
        json={
            "model": "md-sync-image",
            "prompt": "把两张图里的角色放进同一场景",
            "referenceUrls": refs,
            "source": "pytest",
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]
    try:
        pipeline = get_pipeline(KIND_MODEL_DRIVER)

        async def _build() -> object:
            async with get_session_factory()() as session:
                record = await pipeline.store(session).load(job_id)
                assert record is not None
                return await pipeline.build_request(record)

        request = asyncio.run(_build())
        assert tuple(request.reference_urls) == tuple(refs)  # type: ignore[attr-defined]
    finally:
        _cleanup([job_id])


def test_unknown_model_is_rejected(client: TestClient) -> None:
    resp = client.post(
        f"{API}/model-driver/tasks",
        json={"model": "no-such-model", "prompt": "x"},
    )
    assert resp.status_code == 400


def test_catalog_and_driver_endpoints(client: TestClient) -> None:
    models = client.get(f"{API}/model-driver/models")
    assert models.status_code == 200
    assert "md-sync-image" in {item["id"] for item in models.json()["data"]["items"]}

    drivers = client.get(f"{API}/model-driver/drivers")
    assert drivers.status_code == 200
    body = drivers.json()["data"]
    assert {"md_async", "md_sync"} <= {item["name"] for item in body["items"]}
    assert body["errors"] == []


def test_cli_check_passes(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    namespace = argparse.Namespace()
    cmd_list(namespace)
    cmd_check(namespace)
    assert "PASS" in capsys.readouterr().out


def test_retry_requires_confirm_charged(client: TestClient) -> None:
    job = DriverJob(
        name="retry-exit",
        prompt="出口用例",
        model="md-sync-image",
        capability="image",
        status="failed",
        owner_id=_me(client),
        external_id="EXT-DRIVER-CHECK",
        submit_attempts=1,
    )
    job_id = asyncio.run(_insert(job))
    try:
        denied = client.post(f"{API}/model-driver/tasks/{job_id}/retry")
        assert denied.status_code == 409
        ok = client.post(f"{API}/model-driver/tasks/{job_id}/retry?confirmCharged=true")
        assert ok.status_code == 200, ok.text
        body = ok.json()["data"]
        assert body["status"] == "queued"
        assert body["submitAttempts"] == 0
    finally:
        _cleanup([job_id])


def test_facade_persists_resolved_model_id(client: TestClient) -> None:
    """按 label（中文名/展示名）提交时，落库与透传给上游的必须是 **id**。

    路由为了兼容前端历史传参保留了 `label → id` 的别名解析。把原串原样落库，
    后果是路由成功、驱动 404「不认这个模型」——报错文案还会把人引向「模型配错」。
    """
    ticket = asyncio.run(
        get_model_generation().submit(
            SubmitSpec(
                model="Agnes Image 2.5 Flash",
                prompt="别名提交",
                owner_id=_me(client),
                source="alias-test",
            )
        )
    )
    try:
        assert ticket.model == "agnes-image-2.5-flash"
    finally:
        _cleanup([ticket.task_id])


def test_facade_rejects_undeclared_param(client: TestClient) -> None:
    """打错字的参数键必须 400，而不是静默丢弃。

    门面是通用入口，但它知道每个模型声明了什么。漏了这道检查，`{"szie": ...}`
    会被驱动忽略，用户以为改了尺寸、实际用的还是默认值，页面看起来一切正常。
    """
    with pytest.raises(BadRequest) as err:
        asyncio.run(
            get_model_generation().submit(
                SubmitSpec(
                    model="agnes-image-2.5-flash",
                    prompt="参数打错字",
                    owner_id=_me(client),
                    source="param-test",
                    params={"szie": "1024x1024"},
                )
            )
        )
    assert "可用参数" in str(err.value)


def test_facade_enforces_declared_param_range(client: TestClient) -> None:
    """声明的张数上限要在**受理时**就拦，不能等异步任务失败。

    agnes 只支持 n=1。收下 count=4 的后果是：受理成功 → 异步 400 →
    用户只看到一张失败记录，而账单可能已经产生。
    """
    with pytest.raises(BadRequest):
        asyncio.run(
            get_model_generation().submit(
                SubmitSpec(
                    model="agnes-image-2.5-flash",
                    prompt="超出张数",
                    owner_id=_me(client),
                    source="param-test",
                    params={"count": 4},
                )
            )
        )


def test_register_leaves_no_half_provider_on_conflict() -> None:
    """模型 id 撞车时整次注册回滚，不能留下「驱动在册、模型进了一半」。

    旧顺序是先 `add` 供应商、再边循环边校验：撞车抛错时，驱动已进注册表、
    前几个模型已进 `_models`，而 `ensure_drivers_registered` 之后不会再重试。
    表现是清单显示「加载失败」，但它的模型仍能被 `resolve()` 选中并提交。
    """
    from core.registry import RegistryError
    from infra.ai.base import Capability, ModelSpec
    from infra.ai.router import ModelRouter

    class _Provider:
        def __init__(self, name: str, specs: tuple[ModelSpec, ...]) -> None:
            self.name = name
            self.models = specs
            self.capabilities = frozenset({Capability.IMAGE})
            self.supports_poll = False

    router = ModelRouter()
    router.register(
        _Provider(
            "alpha",
            (
                ModelSpec(
                    id="same-id",
                    label="Alpha 模型",
                    capability=Capability.IMAGE,
                    provider="alpha",
                ),
            ),
        )
    )

    clashing = _Provider(
        "beta",
        (
            ModelSpec(
                id="beta-only",
                label="Beta 独占",
                capability=Capability.IMAGE,
                provider="beta",
            ),
            ModelSpec(
                id="same-id",
                label="Beta 冲突",
                capability=Capability.IMAGE,
                provider="beta",
            ),
        ),
    )
    with pytest.raises(RegistryError):
        router.register(clashing)

    assert "beta" not in router.provider_names()
    assert router.resolve("beta-only") is None, "校验失败时不能留下半个供应商"
    assert router.provider_for("same-id").name == "alpha", "原归属不能被抢走"


def test_provider_for_can_refuse_the_only_provider_fallback() -> None:
    """「只有一个供应商」的兜底是给本地联调用的，历史记录不能沾。

    兜底的语义是「模型名没见过，但反正只有一家，就给它」。对**落过库的记录**
    这等于：原渠道的模型被下线/改名之后，重试会把这条任务悄悄打到另一家上——
    产物风格、单价、内容合规口径全变，而且日志里看不出任何异常。

    所以兜底必须是可以关掉的，而不是靠调用方记得「别用未声明的模型名」。
    """
    from core.exceptions import BadRequest
    from infra.ai.base import Capability, ModelSpec
    from infra.ai.router import ModelRouter

    class _Provider:
        name = "alpha"
        capabilities = frozenset({Capability.IMAGE})
        supports_poll = False
        models = (
            ModelSpec(id="a-1", label="Alpha 一", capability=Capability.IMAGE, provider="alpha"),
        )

    router = ModelRouter()
    router.register(_Provider())

    assert router.provider_for("a-1").name == "alpha"
    # 默认行为不变：只有一个供应商时，没声明的模型名照样能取到（本地 echo 联调）
    assert router.provider_for("made-up").name == "alpha"

    with pytest.raises(BadRequest):
        router.provider_for("made-up", allow_fallback=False)
    # 已声明的模型不受影响
    assert router.provider_for("a-1", allow_fallback=False).name == "alpha"
