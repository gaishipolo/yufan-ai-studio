"""后端托管前端产物（`WEB_DIST_DIR`）的守卫。

配了之后后端就是「一个端口同时发页面和接口」，这里守三件容易反向的事：

1. 配了就看得到页面 —— 直接打开或刷新前端路由不能 404（BrowserRouter 靠回退）；
2. 没配就维持原样 —— `/` 仍是 JSON 问候语，不能把 nginx / vite 方案顶掉；
3. 接口拼错仍是 404 JSON —— 回退成页面的话，前端报的是「JSON 解析失败」，
   加一个拼错的路径还会看到空白页，排查方向全被带偏。

用 `TestClient(app)` 而**不进 `with`**：后端托管页面是纯文件服务，不需要 lifespan
里的数据库 / worker，进了反而会在同一个测试进程里再起一轮后台任务。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import _web_dist, create_app
from core.config import settings


def _make_dist(root: Path) -> Path:
    """造一份最小产物：index.html + 带 hash 的 assets + 一个根级文件。"""
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "app-abc123.js").write_text("console.log('studio')", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "index.html").write_text("<!doctype html><title>studio-index</title>", encoding="utf-8")
    return root


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    return _make_dist(tmp_path / "dist")


@pytest.fixture()
def web_client(dist: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "web_dist_dir", str(dist))
    return TestClient(create_app())


def test_root_serves_index_when_configured(web_client: TestClient) -> None:
    res = web_client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "studio-index" in res.text
    # 重新 build 后旧首页会指向已不存在的 hash 文件名（白屏），必须每次回源
    assert "no-cache" in res.headers.get("cache-control", "")


def test_frontend_route_falls_back_to_index(web_client: TestClient) -> None:
    """`/tasks/T001` 磁盘上不存在，但刷新页面必须还能进应用。"""
    res = web_client.get("/tasks/T001")
    assert res.status_code == 200
    assert "studio-index" in res.text


def test_assets_are_served_from_build_output(web_client: TestClient) -> None:
    res = web_client.get("/assets/app-abc123.js")
    assert res.status_code == 200
    assert res.text == "console.log('studio')"


def test_root_level_files_are_served(web_client: TestClient) -> None:
    res = web_client.get("/favicon.svg")
    assert res.status_code == 200
    assert res.text == "<svg/>"


def test_api_typo_stays_json_404(web_client: TestClient) -> None:
    """接口写错不能被首页回退吞掉。"""
    res = web_client.get("/api/v1/no-such-endpoint")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")


def test_whole_api_prefix_is_reserved(web_client: TestClient) -> None:
    """`/api/.env` 这类不在 OpenAPI 里的路径也不能回退成页面。"""
    res = web_client.get("/api/.env")
    assert res.status_code == 404
    assert "studio-index" not in res.text


def test_metrics_and_docs_are_not_swallowed(web_client: TestClient) -> None:
    """/metrics 供 Prometheus 抓取，回退成 HTML 等于把监控打断。"""
    res = web_client.get("/metrics")
    assert res.headers["content-type"] != "text/html"
    assert "studio-index" not in res.text


def test_path_traversal_does_not_escape_dist(web_client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("leaked", encoding="utf-8")
    res = web_client.get("/../secret.txt")
    assert "leaked" not in res.text


def test_unconfigured_backend_keeps_json_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "web_dist_dir", "")
    res = TestClient(create_app()).get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["api"] == settings.api_prefix


def test_dir_without_index_is_treated_as_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「先起后端、再构建前端」期间不该启动失败，也不该把首页变 500。"""
    monkeypatch.setattr(settings, "web_dist_dir", str(tmp_path))
    assert _web_dist() is None
    res = TestClient(create_app()).get("/")
    assert res.status_code == 200
    assert res.json()["service"]
