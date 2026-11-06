"""pytest 全局夹具。

要点：
- 在导入应用包之前锁定环境变量（settings 是 import 期单例）
- 使用独立的临时 sqlite 文件库，测试之间互不污染
- 关闭后台 worker，保证断言可预期
"""

from __future__ import annotations

import os
import tempfile
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
from seed import seed  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _seeded(client: TestClient) -> None:
    import asyncio

    asyncio.run(seed(reset=True))


@pytest.fixture
def api() -> str:
    return "/api/v1"
