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


def test_client_request_id_is_used_only_when_it_looks_like_one(
    client: TestClient, api: str
) -> None:
    """`X-Request-Id` 是客户端可控的，只接受「像个 id」的值（R8-03）。

    这个头会写进该请求的每条日志、并回写到响应头：不限长度会把日志行撑爆，
    不限字符集能让一条日志看起来像两条，可伪造则让两条无关链路在日志里粘在一起。
    """
    good = "req-8f3a_2b7c"
    assert client.get(f"{api}/health", headers={"X-Request-Id": good}).headers[
        "X-Request-Id"
    ] == good

    for bad in ("x" * 300, "short", "<script>alert(1)</script>", "with space and tab\t"):
        resp = client.get(f"{api}/health", headers={"X-Request-Id": bad})
        # 不合法就自己生成，**不报错**——辅助头不该让请求失败
        assert resp.status_code == 200
        assert resp.headers["X-Request-Id"] != bad
        assert len(resp.headers["X-Request-Id"]) == 16
