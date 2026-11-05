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
        "DB_URL": f"sqlite+aiosqlite:///{_TMP_DB.as_posix()}",
        "WORKER_ENABLED": "false",
        "QUEUE_DRIVER": "memory",
        "AI_PROVIDER": "echo",
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
