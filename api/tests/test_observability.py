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
