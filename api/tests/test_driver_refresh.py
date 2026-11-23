"""驱动刷新（阶段 3 / L3）：把**新增**的驱动文件热接上。

刷新要守住的边界（这几条是刻意的选择，不是没做完）：

1. **加文件 = 热生效**：新驱动被 import、校验、注册，供应商专属端点也会挂上；
2. **改已加载的文件 = 需重启**：不热替换（`reload` 会丢 in-flight 实例状态），
   只报出来提醒重启 —— 假装生效比不生效更坏；
3. **刷新是进程级的**：只影响本实例，多实例靠各自的周期刷新收敛；
4. **坏配置立刻暴露**：刷新连带重跑必填项与提交预算预检。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from modules.model_driver import module, registry
from modules.model_driver.permissions import PERM_REFRESH

# --------------------------------------------------------------------------- #
# 假驱动
# --------------------------------------------------------------------------- #

_FAKE_DRIVER = '''
"""假驱动（测试用）。"""

from infra.ai.base import Capability, GenerationProgress, GenerationRequest, ModelSpec


class Driver:
    name = "{name}"
    capabilities = frozenset({{Capability.IMAGE}})
    models = (
        ModelSpec(
            id="{model}",
            label="刷新测试驱动",
            capability=Capability.IMAGE,
            provider="{name}",
        ),
    )
    supports_poll = False

    async def submit(self, request: GenerationRequest) -> str:
        return "ext-1"

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        return GenerationProgress(state="succeeded")

    async def cancel(self, external_id: str) -> None:
        return None
'''

_FAKE_DRIVER_WITH_JOB = _FAKE_DRIVER + '''
from modules.model_driver.spec import JobSpec

EXTRA_JOBS = (JobSpec(name="{name}.job"),)
'''


def _write_driver(tmp_path: Path, name: str, *, with_job: bool = False) -> str:
    """在临时目录写一个假驱动文件，返回模块名。

    模块名带上目录名做唯一后缀：`sys.modules` 会缓存模块，同名会让上一个
    测试的代码「留在原地」，测出来的就��假东西。
    """
    module_name = f"{name}_{tmp_path.name.replace('-', '_')}"
    source = (_FAKE_DRIVER_WITH_JOB if with_job else _FAKE_DRIVER).format(
        name=module_name,
        model=f"{module_name}-1",
    )
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")
    return module_name


def _use_drivers(monkeypatch: pytest.MonkeyPatch, *module_names: str) -> None:
    """让 registry 只看得到这几个驱动文件。"""
    monkeypatch.setattr(
        registry,
        "_iter_driver_modules",
        lambda: [(name, name) for name in module_names],
    )


@pytest.fixture
def _restore_driver_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """把驱动状态还原到「服务已启动」的基线。

    刷新会改**进程级**的全局状态（驱动清单、路由单例）。不还原的话，
    后面依赖全局路由的测试会拿到一个空路由 —— 表现为莫名其妙的
    「未知模型 'xxx'」，排查起来看不出跟本文件有关系。
    """
    from infra.ai import reset_model_router

    # 干净起点：先清掉上一次测试留下的驱动状态。
    reset_model_router()
    registry.reset_drivers()
    # 基线 = 「服务已经在跑」：真实驱动已加载并注册。
    # 没有这个基线的话，刷新会把所有文件都当成「新增」—— 那个进程压根没扫描过，
    # 新文件对它不是「热接上」，是「还没加载」。
    registry.ensure_drivers_registered()
    yield
    # 先撤销 monkeypatch：否则重建基线时会把假驱动也扫进来，
    # 留下一条永久性的加载错误污染后面的测试。
    monkeypatch.undo()
    reset_model_router()
    registry.reset_drivers()
    # 重建基线：真实驱动重新注册进路由单例。
    registry.ensure_drivers_registered()
    # 临时假驱动模块不留痕：`sys.modules` 里的模块名会被后面的测试看到。
    for name in [
        n for n in sys.modules if n.startswith(("refresh_fake_", "broken_driver_"))
    ]:
        sys.modules.pop(name, None)


# --------------------------------------------------------------------------- #
# 1. 加文件 = 热生效
# --------------------------------------------------------------------------- #


def test_refresh_hot_loads_a_new_driver_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """新增的驱动文件被热接上（不用重启）。"""
    from infra import get_model_router

    module_name = _write_driver(tmp_path, "refresh_fake")
    monkeypatch.syspath_prepend(str(tmp_path))
    _use_drivers(monkeypatch, module_name)

    result = registry.refresh(operator="u_test")

    assert module_name in result.added, f"新增驱动没热接上：{result}"
    assert module_name in get_model_router().provider_names()
    assert result.operator == "u_test"
    assert result.refreshed_at, "刷新时刻必须记下来（审计用）"


def test_refresh_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """再刷一次不会重复注册（added 为空，kept 里是它）。"""
    module_name = _write_driver(tmp_path, "refresh_fake")
    monkeypatch.syspath_prepend(str(tmp_path))
    _use_drivers(monkeypatch, module_name)

    first = registry.refresh()
    second = registry.refresh()

    assert first.added == (module_name,)
    assert second.added == (), f"第二次刷新又接了一遍：{second}"
    assert module_name in second.kept


def test_refresh_keeps_working_drivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """刷新不换掉已在跑的驱动实例。"""
    from infra import get_model_router

    module_name = _write_driver(tmp_path, "refresh_fake")
    monkeypatch.syspath_prepend(str(tmp_path))
    _use_drivers(monkeypatch, module_name)

    registry.refresh()
    before = get_model_router().provider_for(f"{module_name}-1")
    registry.refresh()
    after = get_model_router().provider_for(f"{module_name}-1")

    assert before is after, "刷新把正在跑的实例换掉了"


# --------------------------------------------------------------------------- #
# 2. 改已加载的文件 = 需重启（只提醒，不假装生效）
# --------------------------------------------------------------------------- #


def test_refresh_reports_changed_file_without_replacing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """文件改了要报出来，但**不热替换**。

    `importlib.reload` 会丢 in-flight 实例状态（正在跑的请求、已提交的
    `external_id` 指向的旧类），收益不抵风险。所以这里只提醒重启。
    """
    from infra import get_model_router

    module_name = _write_driver(tmp_path, "refresh_fake")
    monkeypatch.syspath_prepend(str(tmp_path))
    _use_drivers(monkeypatch, module_name)
    registry.refresh()
    before = get_model_router().provider_for(f"{module_name}-1")

    # 改文件内容（改成另一个版本）
    _write_driver(tmp_path, "refresh_fake")
    result = registry.refresh()

    assert module_name in result.changed_files, f"改了文件没报出来：{result}"
    after = get_model_router().provider_for(f"{module_name}-1")
    assert before is after, "改了文件就热替换了 —— 这会丢在跑的实例状态"


# --------------------------------------------------------------------------- #
# 3. 出问题要报出来，不能连累别的驱动
# --------------------------------------------------------------------------- #


def test_refresh_reports_a_broken_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """坏驱动只进 errors，不拖垮整次刷新。"""
    module_name = _write_driver(tmp_path, "refresh_fake")
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "broken_driver.py").write_text("Driver = None\n", encoding="utf-8")
    _use_drivers(monkeypatch, module_name, "broken_driver")

    result = registry.refresh()

    assert module_name in result.added, "好驱动被坏文件连累了"
    assert any("broken_driver" in problem for problem in result.errors), (
        f"坏驱动没报出来：{result}"
    )


def test_refresh_reports_a_missing_job_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """声明了作业却没有 handler → 报出来。

    handler 是 import 期装饰器：驱动模块被 import 时就该注册好。缺了它，
    作业会一直停在 queued —— 启动期会因此让服务起不来，运行期只能报出来。
    """
    module_name = _write_driver(tmp_path, "refresh_fake", with_job=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    _use_drivers(monkeypatch, module_name)

    result = registry.refresh()

    assert f"{module_name}.job" in result.missing_handlers, (
        f"缺 handler 没报出来：{result}"
    )


# --------------------------------------------------------------------------- #
# 4. 刷新连带重跑配置巡检
# --------------------------------------------------------------------------- #


def test_refresh_reruns_the_config_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _restore_driver_state: None
) -> None:
    """配置被改坏要在刷新时就发现，而不是等下次重启。"""
    from infra import driver_config

    def _boom(_app_env: str) -> None:
        raise RuntimeError("缺少必填项 GRSAI_API_KEY")

    monkeypatch.setattr(driver_config, "ensure_prod_driver_settings", _boom)
    module_name = _write_driver(tmp_path, "refresh_fake")
    monkeypatch.syspath_prepend(str(tmp_path))
    _use_drivers(monkeypatch, module_name)

    result = registry.refresh()

    assert any("必填项巡检失败" in problem for problem in result.errors), (
        f"巡检没重跑：{result}"
    )


# --------------------------------------------------------------------------- #
# 5. 端点与周期任务
# --------------------------------------------------------------------------- #


def test_refresh_endpoint_is_protected_by_its_own_permission() -> None:
    """刷新端点挂的是**运维级**权限码，不是挂在 read 上。

    看模型目录的人不该能改「进程里装了什么驱动」—— 挂错码等于把运维能力
    送给了所有只读用户。
    """
    from api.routing import iter_api_routes

    codes: set[str] = set()
    found = False
    for route, path in iter_api_routes(module.router.routes):
        if path != "/model-driver/refresh":
            continue
        found = True
        for dependant in route.dependant.dependencies:
            required = getattr(dependant.call, "required_permissions", None)
            if required:
                codes.update(required)
    assert found, "没找到刷新端点"
    assert PERM_REFRESH in codes, f"刷新端点的权限码不对：{codes}"
    assert "model_driver:read" not in codes, "刷新端点不该挂在 read 上"


def test_refresh_permission_is_declared() -> None:
    """权限点要登记在模块的权限清单里（否则启动期核对会报缺口）。"""
    from modules.model_driver.permissions import SPECS

    declared = {spec.code for spec in SPECS}
    assert PERM_REFRESH in declared


def test_refresh_periodic_job_is_declared() -> None:
    """周期刷新要在模块的周期任务清单里声明（声明了却没注册 = 启动失败）。"""
    assert "model_driver.drivers.refresh" in module.periodic


def test_refresh_periodic_job_is_registered() -> None:
    """周期任务真的注册了（不是只在清单里写了个名字）。"""
    from core.scheduler import periodic_jobs

    names = {job.name for job in periodic_jobs()}
    assert "model_driver.drivers.refresh" in names


def test_refresh_periodic_runs_on_every_instance() -> None:
    """周期刷新必须**每实例**都跑（`leader_only=False`）。

    刷新是进程级的：多实例部署时端点只影响被打到的那个实例，其余实例要靠
    自己的周期刷新收敛。跑在一个实例上 = 另一个实例永远不刷新。
    """
    from core.scheduler import periodic_jobs

    job = next(j for j in periodic_jobs() if j.name == "model_driver.drivers.refresh")
    assert job.leader_only is False, "周期刷新只在 leader 上跑 —— 多实例收不了敛"
