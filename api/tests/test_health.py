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


def test_request_context_does_not_leak_current_user_between_requests() -> None:
    """同一个任务里连处理两个请求，第二个不能看到第一个的用户。

    用裸 ASGI 连调两次，模拟「服务器在同一个 task 里顺序处理请求」（嵌入式服务、
    测试客户端、部分网关都这么做）。`current_user` 只在**认证成功**的分支写入，
    所以没带令牌的那个请求不会覆盖它——不复位就会读到上一个登录用户，
    症状是审计/日志把这次操作记在别人头上，且只在特定连接形态下偶发。
    """
    import asyncio

    from api.middleware import RequestContextMiddleware
    from core.context import CurrentUser, get_current_user, set_current_user

    seen: list[CurrentUser | None] = []

    async def app(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if not seen:  # 只有第一个请求「登录」了
            set_current_user(CurrentUser(id="u_1", name="甲"))
        seen.append(get_current_user())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, object]:  # pragma: no cover - 中间件不读 body
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:  # type: ignore[no-untyped-def]
        return None

    middleware = RequestContextMiddleware(app)

    async def call_twice() -> None:
        for _ in range(2):
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/x",
                "headers": [],
                "client": ("127.0.0.1", 1),
            }
            await middleware(scope, receive, send)

    asyncio.run(call_twice())

    assert [user and user.id for user in seen] == ["u_1", None]
    # 收尾：别把当前用户留在 pytest 自己的上下文里
    set_current_user(None)
