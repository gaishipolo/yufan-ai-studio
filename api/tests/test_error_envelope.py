"""错误响应信封：任何异常都必须收敛成 `{code, message, data}`。

框架的承诺是「路由与 service 层可以放心抛异常」——这条承诺成立的前提是
**每一种异常出口都被处理器接住**。漏接一个的后果不是崩溃，是返回一坨
不属于这个契约的东西：Starlette 默认的纯文本、SQLAlchemy 的堆栈、或者
FastAPI 自己的 `{"detail": ...}`。前端统一按 `code` 判断，拿到这些就只会弹
「未知错误」。

## 这里守的是什么

1. 信封三字段恒定存在（前端 `code === 0` 判成功、否则展示 `message`）；
2. `code` 的段位能被反推出 HTTP 状态（`code // 100`），前端据此做降级处理；
3. `code == 0` 只用于成功，错误响应的 code 永远非零。

变异扫描发现：把 `exc.status_code * 100` 改成 `* 10`，现有 10 条错误码测试
全绿——`test_module_errors.py` 只覆盖**模块自定义码**的号段规则，不覆盖
框架把 Starlette 的 404/405 转成信封这一段。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.exceptions import ErrorCode


def _envelope(response) -> dict:
    body = response.json()
    assert isinstance(body, dict)
    # 三字段恒定：data 可以为 None，但键必须存在（前端直接读 body.data）
    assert {"code", "message", "data"} <= set(body)
    assert isinstance(body["code"], int)
    assert body["code"] != 0
    return body


def test_unknown_route_is_404_with_envelope(client: TestClient, api: str) -> None:
    response = client.get(f"{api}/definitely-not-a-route")

    body = _envelope(response)
    assert response.status_code == 404
    assert body["code"] == ErrorCode.NOT_FOUND
    assert body["code"] // 100 == 404


def test_wrong_method_is_405_with_envelope(client: TestClient, api: str) -> None:
    """Path 存在但方法不对：走的是 Starlette 分支，同样要变成信封。"""
    response = client.get(f"{api}/identity/login")  # 该端点是 POST

    body = _envelope(response)
    assert response.status_code == 405
    assert body["code"] // 100 == 405


def test_validation_error_names_the_offending_field(client: TestClient, api: str) -> None:
    """422 必须带 `details[].field`：只说「参数不合法」，前端无法定位到输入框。"""
    response = client.get(f"{api}/assets", params={"page": "abc"})

    body = _envelope(response)
    assert response.status_code == 422
    assert body["code"] == ErrorCode.VALIDATION_FAILED
    fields = [item["field"] for item in body["details"]]
    assert "page" in fields


def test_validation_envelope_is_the_known_segment_exception(client: TestClient, api: str) -> None:
    """**唯一一处** `code // 100` 与 HTTP 状态不一致：40001 配 422。

    `ErrorCode.VALIDATION_FAILED = 40001` 属于 400 段（`04` = 参数类错误），
    但 FastAPI 用 422 表达「语法正确、语义不合法」。两条规则在这里必然打架。

    钉住它是为了让现状**可见**：要么将来把验证失败改成 42201，要么把这段
    文档改成「400 段 = 调用方错误」——不论怎么改，这条断言都会提醒改的人
    前端有 `code // 100` 这个用法（见 `studio/src/shared/api/http.ts`）。
    """
    response = client.get(f"{api}/assets", params={"page": "abc"})

    assert response.status_code == 422
    assert response.json()["code"] // 100 == 400
    assert response.json()["code"] // 100 != response.status_code


def test_business_not_found_uses_the_exact_code(client: TestClient, api: str) -> None:
    """`AppError` 走的是另一条分支：码来自类定义，不是算出来的。"""
    response = client.get(f"{api}/assets/{'0' * 32}")

    body = _envelope(response)
    assert response.status_code == 404
    assert body["code"] == ErrorCode.NOT_FOUND


def test_success_envelope_uses_code_zero(client: TestClient, api: str) -> None:
    """**成功响应有两种形状并存**，钉住其中一种必须是 `code == 0`。

    现状：多数接口直接返回裸数据，而 identity 的 `/me` 返回
    `{code: 0, message: "OK", data: {...}}`。前端 `http.ts` 靠「响应体里有
    `code` 就按信封解包」兼容两种（`studio/src/shared/api/http.ts`）。

    这个兼容层是有代价的：如果哪个接口返回的**业务对象**恰好带一个 `code`
    字段（错误码字典、外部任务回执之类），前端会把它当信封，读 `data` 得到
    `undefined`，页面空白但网络面板 200。所以 `code == 0` 必须钉住——
    信封里的 code 一旦非零，前端直接抛错。

    统一成一种形状是独立改动（改一半接口或改前端契约），不在这轮。
    """
    response = client.get(f"{api}/identity/me")

    body = response.json()
    assert response.status_code == 200
    assert body["code"] == ErrorCode.OK
    # data 是真正的数据，不是套了两层信封
    assert "data" in body
    assert isinstance(body["data"], dict)
    assert "code" not in body["data"]
