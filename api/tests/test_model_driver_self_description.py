"""驱动自描述机制（阶段 2 / L2）。

框架的公共层不认识供应商：供应商专属的路由、作业、出参都由**驱动文件自己**
声明（`EXTRA_ROUTES` / `EXTRA_JOBS` / `EXTRA_SCHEMAS`），框架只负责收集与装配。

这组测试钉住三件事：

1. 驱动自报的东西真的挂上了，而且只挂一次；
2. 模块声明的作业来自驱动自报，公共层不写死供应商的作业名；
3. 自报的内容不合法时**启动失败**，而不是端点凭空消失。
"""

from __future__ import annotations

import sys
import types

import pytest

from api.routing import iter_api_routes
from infra.ai.base import Capability, ModelSpec
from modules.model_driver import module, registry
from modules.model_driver.libtv import worker as libtv_worker
from modules.model_driver.spec import ExtrasError, JobSpec


def _mounted_paths() -> list[str]:
    """模块 router 摊平后的路径。

    必须摊平：FastAPI 0.162 的 `include_router` 是延迟包含，直接遍历
    `router.routes` 只会看到一个占位对象，断言会「永远通过」而不是报错。
    """
    return [path for _route, path in iter_api_routes(module.router.routes)]


# --------------------------------------------------------------------------- #
# 1. 自报的东西挂上了，且只挂一次
# --------------------------------------------------------------------------- #


def test_driver_declared_routes_are_mounted() -> None:
    """驱动自报的端点挂到了模块 router 上。

    对外路径仍是 `/model-driver/libtv/...`：子 router 自带 `/libtv` 前缀，
    模块 router 再并上 `/model-driver`。
    """
    paths = _mounted_paths()
    for suffix in (
        "/model-driver/libtv/account",
        "/model-driver/libtv/progress",
        "/model-driver/libtv/consumption",
    ):
        assert suffix in paths, f"驱动自报的端点没挂上：{suffix}（实际 {paths}）"


def test_driver_declared_routes_are_mounted_only_once() -> None:
    """重复扫描不会把同一批端点挂两遍。

    `include_router` 是**复制**路由：挂两次就是同一批端点出现两遍 —— 文档里
    同一个接口出现两份，OpenAPI 也会变形。
    """
    before = _mounted_paths()
    registry.ensure_drivers_registered()
    registry.ensure_drivers_registered()
    assert _mounted_paths() == before


def test_rescan_does_not_duplicate_routes() -> None:
    """`reset_drivers()` 后重新扫描也不重复挂。

    重扫会拿到同一批 router 对象，所以按对象身份去重就够 —— 不需要按名字。
    """
    before = _mounted_paths()
    registry.reset_drivers()
    registry.ensure_drivers_registered()
    after = _mounted_paths()
    assert after == before, f"重扫后端点变了：{before} → {after}"


# --------------------------------------------------------------------------- #
# 2. 作业与出参来自驱动自报
# --------------------------------------------------------------------------- #


def test_module_jobs_come_from_driver_declarations() -> None:
    """模块声明的作业来自驱动自报，公共层不写死供应商的作业名。"""
    names = registry.job_names()
    assert libtv_worker.JOB_DOWNLOAD in names
    assert module.jobs == names


def test_extra_schemas_come_from_driver_declarations() -> None:
    """供应商专属出参由驱动自报并被收集。"""
    names = {schema.__name__ for schema in registry.extra_schemas()}
    assert "LibTvAccountOut" in names
    assert "LibTvConsumptionOut" in names


def test_extra_jobs_must_be_job_specs() -> None:
    """作业声明的形状由框架校验（不是随便塞个字符串进来）。"""
    with pytest.raises(ExtrasError):
        registry._read_extra_jobs(types.SimpleNamespace(EXTRA_JOBS=("libtv.download",)))


# --------------------------------------------------------------------------- #
# 3. 自报内容不合法 → 启动失败（不是端点凭空消失）
# --------------------------------------------------------------------------- #


def test_malformed_extra_routes_are_rejected() -> None:
    """路由必须是 APIRouter。"""
    with pytest.raises(ExtrasError):
        registry._read_extra_routes(types.SimpleNamespace(EXTRA_ROUTES=("nope",)))


def test_malformed_extra_schemas_are_rejected() -> None:
    """出参必须是 BaseModel 子类。"""
    with pytest.raises(ExtrasError):
        registry._read_extra_schemas(types.SimpleNamespace(EXTRA_SCHEMAS=(dict,)))


def test_extra_routes_must_be_a_tuple() -> None:
    """声明容器是 tuple —— 避免「写成了 list 但没人发现」这种半生效状态。"""
    with pytest.raises(ExtrasError):
        registry._read_extra_routes(types.SimpleNamespace(EXTRA_ROUTES=[]))


def test_broken_extras_are_not_swallowed_as_driver_errors() -> None:
    """自报内容不合法 → 抛 ExtrasError，而不是记成「这个驱动加载失败」。

    两者后果完全不同：前者是**启动失败**（端点不许凭空消失），后者是
    「只红自己这一条」（第三方驱动不该拖垮整个服务）。这条纪律一旦被当成
    后者，端点就会安静地少掉，前端只看到一个没头没脑的 404。
    """
    from infra.ai.base import GenerationProgress, GenerationRequest

    class _FakeDriver:
        name = "broken_test"
        capabilities = frozenset({Capability.IMAGE})
        models = (
            ModelSpec(
                id="broken-test-1",
                label="坏驱动",
                capability=Capability.IMAGE,
                provider="broken_test",
            ),
        )
        supports_poll = False

        async def submit(self, request: GenerationRequest) -> str:
            return "x"

        async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
            return GenerationProgress(state="succeeded")

        async def cancel(self, external_id: str) -> None:
            return None

    fake = types.ModuleType("modules.model_driver.drivers._broken_test")
    fake.Driver = _FakeDriver
    fake.EXTRA_ROUTES = ("not a router",)
    sys.modules["modules.model_driver.drivers._broken_test"] = fake
    try:
        original = registry._iter_driver_modules
        registry._iter_driver_modules = lambda: [  # type: ignore[assignment]
            ("broken_test", "modules.model_driver.drivers._broken_test")
        ]
        try:
            with pytest.raises(ExtrasError):
                registry._discover()
        finally:
            registry._iter_driver_modules = original  # type: ignore[assignment]
    finally:
        sys.modules.pop("modules.model_driver.drivers._broken_test", None)


def test_job_spec_is_the_public_contract() -> None:
    """`JobSpec` 与 `ExtrasError` 都在公开契约里（驱动作者要 import 它们）。"""
    assert JobSpec(name="x").name == "x"
    assert issubclass(ExtrasError, RuntimeError)
