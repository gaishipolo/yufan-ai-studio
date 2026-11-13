"""pytest 全局夹具。

要点：
- 在导入应用包之前锁定环境变量（settings 是 import 期单例）
- 使用独立的临时 sqlite 文件库，测试之间互不污染
- 关闭后台 worker，保证断言可预期
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

_TMP_DB = Path(tempfile.mkdtemp(prefix="app-test-")) / "test.db"

os.environ.update(
    {
        "APP_ENV": "test",
        "DEBUG": "false",
        # 不读 `api/.env`：那份文件是开发者本机的部署参数（存储驱动、S3 凭据、
        # 共享盘路径）。读它会让测试结果取决于谁在什么机器上跑——本机把
        # STORAGE_DRIVER 切成 s3、或者填了 S3_*，`test_config_guard` 与素材接口
        # 用例就会翻脸。指向一个不存在的文件，等价于「测试只认下面这些显式值」。
        "SETTINGS_ENV_FILE": str(_TMP_DB.parent / "no-such.env"),
        "DB_URL": f"sqlite+aiosqlite:///{_TMP_DB.as_posix()}",
        "WORKER_ENABLED": "false",
        # 周期任务会在后台每 N 秒跑一次，测试里关掉，避免「断言时刚好被巡检动过」
        "SCHEDULER_ENABLED": "false",
        "QUEUE_DRIVER": "memory",
        # 存储/归档驱动必须钉死：`settings` 会读 `api/.env`，开发者本机把
        # STORAGE_DRIVER 切成 s3 之后，测试会去连他那个桶（或者因为没有网络而红），
        # 「本机绿、CI 红」就是这么来的。测试永远是本地盘。
        "STORAGE_DRIVER": "local",
        "ARCHIVE_DRIVER": "local",
        "AI_PROVIDER": "echo",
        "AI_PROVIDERS": "echo",
        # 测试永远使用临时磁盘，不能读写开发环境 Z 盘的真实素材。
        "STORAGE_LOCAL_ROOT": str(_TMP_DB.parent / "storage"),
        "MEDIA_ARCHIVE_ROOT": str(_TMP_DB.parent),
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import create_app  # noqa: E402
from core.scheduler import periodic_jobs, restore_periodic_jobs  # noqa: E402
from seed import seed  # noqa: E402

# create_app() 之后才能取到完整的周期任务注册表（模块在发现时注册）。
_PERIODIC_SNAPSHOT: tuple = ()


@pytest.fixture(scope="session")
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _seeded(client: TestClient) -> None:
    import asyncio

    global _PERIODIC_SNAPSHOT
    asyncio.run(seed(reset=True))
    _PERIODIC_SNAPSHOT = periodic_jobs()


@pytest.fixture(scope="session", autouse=True)
def _dispose_engine_at_exit() -> Iterator[None]:
    """整场跑完后关掉引擎，别把 sqlite 连接留给 GC。

    为什么必须有：`TestClient` 有自己的事件循环，`asyncio.run(seed(...))` 又一个。
    连接池里的 `aiosqlite` 连接是**在某个循环里建**的，循环关闭后连接对象仍在池里；
    等解释器 GC 到它时 `Connection.__del__` 会在已关闭的循环上收尾，
    打出 `RuntimeError: Event loop is closed` 与 `ResourceWarning`。

    这个现象在本地只是 14 条告警（`python -X dev` 可见），但在 GitHub Actions 上
    会被日志解析器标成 **2 条 failure annotation**——构建明明是绿的，PR 页面却挂着
    两个红叉。它训练出来的行为是「annotation 不用看」，那比告警本身危险得多。

    `dispose_engine()` 会 `await engine.dispose()` 并把全局单例置空，所以它在
    `client` 之后执行（session 级夹具的拆解顺序与创建顺序相反）。
    """
    yield
    import asyncio

    from db.session import dispose_engine

    asyncio.run(dispose_engine())


@pytest.fixture(autouse=True)
def _assembled_ports() -> None:
    """每个用例前把声明式注入口装配齐。

    引用检查是 fail-closed 的：某个用例为了验「未注册时拒绝删除」而清空注册表后，
    下一个用例必须重新按 `ModuleSpec` 声明装配，否则会连锁 503。把恢复动作放在
    全局夹具里，测试就不必各写一遍 finally，也不会因为漏写而互相污染。
    """
    from api.discovery import active_modules
    from core.ports.library_references import bind_library_reference_sources

    for spec in active_modules():
        if spec.library_reference_sources:
            bind_library_reference_sources(spec.name, spec.library_reference_sources)
    if _PERIODIC_SNAPSHOT:
        restore_periodic_jobs(_PERIODIC_SNAPSHOT)


@pytest.fixture
def api() -> str:
    return "/api/v1"


@pytest.fixture(autouse=True)
def _restore_settings() -> Iterator[None]:
    """每个用例结束后按环境变量重新加载配置。

    为什么必须放在 autouse 里：`monkeypatch` 的 `setenv` 在**夹具拆完之后**才撤销，
    所以任何 `reload_settings()`（或"改了 env 立刻生效"的第三方路径）都会把临时值
    留进单例。autouse 夹具在 `monkeypatch` **之后**拆，此时环境变量已经回到基线，
    重载一次就把进程恢复干净。

    没有这一步的症状：某个用例设了 `DB_URL` / `BACKUP_DIR`，后面的用例莫名连到临时库，
    或者配置读出来自相矛盾——`get_settings()` 曾经是 `lru_cache`，`cache_clear()`
    之后返回新对象，而所有 import 过 `settings` 的模块还绑着旧对象。
    """
    yield
    from core.config import reload_settings

    reload_settings()


@pytest.fixture(autouse=True)
def _clean_rate_limit_counters() -> None:
    """每个用例前清空限流计数（计数器就在缓存里）。

    限流是**生产保护**，不该变成测试之间的隐式耦合：`client` 是 session 级的，
    所有用例复用同一批演示账号，上一个用例刷满的额度会让下一个用例直接收 429，
    而失败信息读起来像是「这个接口坏了」。清掉计数比把额度调大更诚实——
    调大只是把崩溃点往后推，谁的用例再多跑几轮又会红。

    代价是「跨用例累积额度」这类问题测不出来；那种行为由 `test_ratelimit.py`
    （直接构造 `RateLimiter`）和 `test_global_ratelimit.py`（单独起实例）覆盖。
    """
    import asyncio

    from infra import get_cache

    asyncio.run(get_cache().clear())


@pytest.fixture
def example_drivers() -> Iterator[None]:
    """显式装上示例驱动，用例结束后摘干净。

    示例驱动的文件名带 `_` 前缀，所以**不会自动载入**——这是刻意的：
    它们是给开发者看的骨架，不该出现在用户可见的模型目录里（以前叫
    `mock_async.py` / `mock_sync.py`，于是真被扫进来了，用户在生成工作台
    里能选中「示例同步生图」，提交后只拿到一张占位图）。

    但框架自己的用例需要一个**离线可跑**的供应商——不能真调外部厂商——
    所以留了这个显式入口。

    摘掉是必须的：模型路由是进程单例，示例驱动装上后会漏给后面的用例，
    而「示例模型不在公开目录里」那条断言会被自己人破坏。
    """
    from modules.model_driver.registry import (
        register_example_drivers,
        unregister_example_drivers,
    )

    register_example_drivers()
    try:
        yield
    finally:
        unregister_example_drivers()

