"""可观测性与探针语义：/metrics 的访问控制、/ready 的三档结论。

这些用例守的是「运维看到的信号是否可信」：
- 指标端点不能因为抓取方是机器就裸奔；
- 就绪探针不能把「有东西在漏」报成「服务挂了」，也不能反过来。
"""

from __future__ import annotations

from modules.health.checks import RuntimeCheck, RuntimeStatus


def test_runtime_status_distinguishes_degraded_from_down() -> None:
    healthy = RuntimeStatus(checks=[RuntimeCheck("database", True, "SELECT 1 成功")])
    assert healthy.status == "ok"

    # 缓存不可用只是降级：限流会放行、热点回源，但请求仍然正确
    degraded = RuntimeStatus(
        checks=[
            RuntimeCheck("database", True, "ok"),
            RuntimeCheck("cache", False, "连不上", required=False),
        ]
    )
    assert degraded.status == "degraded"
    assert not degraded.down

    # 数据库不可用是 down：编排系统必须摘掉这个实例
    down = RuntimeStatus(checks=[RuntimeCheck("database", False, "refused")])
    assert down.status == "down"
    assert down.down


def test_route_template_replaces_only_the_parameter_segment() -> None:
    """标签里的参数位必须整段替换，不能做子串替换。

    子串替换会把前缀里相同的字符一起改掉：`/api/v1/tasks/1` + `task_id="1"`
    曾经变成 `/api/v{task_id}/tasks/{task_id}`——标签本身失去意义，而且两条
    不同的路由可能因此合并成同一条时间序列（看板上的 P99 就成了两个接口的混合）。
    """
    from api.instrumentation import route_template

    def template(path: str, actual: str, params: dict[str, str]) -> str:
        route = type("_Route", (), {"path": path})()
        return route_template({"route": route, "path": actual, "path_params": params})

    # id 恰好等于前缀里的 "1"：只有参数位该被替换
    assert template("/tasks/{task_id}", "/api/v1/tasks/1", {"task_id": "1"}) == (
        "/api/v1/tasks/{task_id}"
    )
    # 参数值恰好等于前缀里的 "v1"
    assert template(
        "/assets/{asset_id}/preview", "/api/v1/assets/v1/preview", {"asset_id": "v1"}
    ) == "/api/v1/assets/{asset_id}/preview"
    # 多个参数
    assert template(
        "/packs/{pack_id}/items/{item_id}",
        "/api/v1/packs/p1/items/i2",
        {"pack_id": "p1", "item_id": "i2"},
    ) == "/api/v1/packs/{pack_id}/items/{item_id}"
    # 静态路由：真实路径就是模板 + 前缀
    assert template("/health", "/api/v1/health", {}) == "/api/v1/health"
    # 段数对不齐（`:path` 转换器把 `/` 吃进参数）：不猜，退回路由自己的模板
    assert template(
        "/files/{p:path}", "/api/v1/files/a/b", {"p": "a/b"}
    ) == "/files/{p:path}"


def test_metrics_endpoint_lists_core_families(client) -> None:
    client.get("/api/v1/health")
    client.get("/definitely-not-a-route")  # 未匹配请求必须归到 unmatched
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "queue_jobs",
        "outbox_events",
        "jobs_processed_total",
        "app_build_info",
    ):
        assert name in body, f"缺少指标 {name}"
    # 标签用路由模板，不是真实路径
    assert 'route="/api/v1/health"' in body
    assert 'route="unmatched"' in body


def test_metrics_requires_bearer_token_in_prod(client, monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "metrics_token", "s3cret")

    assert client.get("/metrics").status_code == 403
    # 只认请求头：query 里的 token 会进访问日志与代理日志
    assert client.get("/metrics?token=s3cret").status_code == 403
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    )


def test_metrics_disabled_looks_absent(client, monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "metrics_enabled", False)
    # 关闭时 404 而不是 403：不对外暴露「这里本来有个端点」
    assert client.get("/metrics").status_code == 404


def test_ready_reports_each_dependency(client) -> None:
    payload = client.get("/api/v1/health/ready").json()["data"]
    assert payload["status"] in {"ok", "degraded"}
    names = {check["name"] for check in payload["checks"]}
    assert {"database", "queue", "cache", "outbox.pending"} <= names
    # 兼容字段仍在：老探针配置不需要改
    assert payload["database"] == "ok"
    assert isinstance(payload["queueSize"], int)


def test_debug_flag_opens_project_loggers_not_third_party() -> None:
    """`DEBUG=true` 必须真的打开 core / infra / db 的 debug 日志。

    早先只对 `app` 命名空间设了级别——而 `app` 是访问日志那一支，
    `core.*` / `infra.*` 的 logger 不以它开头，于是「打开了 debug 却一条 debug
    日志都看不到」，排查时容易误判成「那些代码里没写日志」。
    同时第三方库不能被一起放开：SQL 语句刷屏会把真正的线索埋掉。
    """
    import logging

    from core.logging import setup_logging

    watched = (
        "app",
        "api",
        "core",
        "db",
        "infra",
        "modules",
        "generation",
        "workers",
        "scaffold",
        "sqlalchemy.engine.Engine",
    )
    root = logging.getLogger()
    saved_levels = {name: logging.getLogger(name).level for name in watched}
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    try:
        setup_logging("dev", debug=True)
        for name in ("core.ratelimit", "infra.http", "db.outbox"):
            # 断言「生效级别」而不是 `isEnabledFor`：后者还受进程级
            # `logging.disable()` 影响，而那是运行器（pytest 捕获）的东西，
            # 不是这份配置的产物。这里要守的是「DEBUG 有没有落到项目命名空间」。
            assert logging.getLogger(name).getEffectiveLevel() == logging.DEBUG, name
        # 第三方库仍被压住：SQL echo 级别的日志会把真正的线索埋掉
        assert logging.getLogger("sqlalchemy.engine.Engine").level == logging.WARNING

        setup_logging("dev", debug=False)
        assert logging.getLogger("core.ratelimit").getEffectiveLevel() == logging.INFO
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_root_level)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)
