"""健康检查与统一响应约定。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient, api: str) -> None:
    body = client.get(f"{api}/health").json()
    assert body["code"] == 0
    assert body["data"]["status"] == "ok"


def test_ready(client: TestClient, api: str) -> None:
    body = client.get(f"{api}/health/ready").json()
    assert body["data"]["database"] == "ok"


def test_envelope_and_request_id(client: TestClient, api: str) -> None:
    resp = client.get(f"{api}/tasks")
    assert {"code", "message", "data"} <= resp.json().keys()
    assert resp.headers["X-Request-Id"]
    assert "X-Process-Time-Ms" in resp.headers


def test_unknown_route_keeps_shape(client: TestClient, api: str) -> None:
    resp = client.get(f"{api}/not-a-real-endpoint")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40400
