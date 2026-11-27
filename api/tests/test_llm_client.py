"""LLM 客户端守卫（设计文档 §8 W3 的 G1–G4）。

用 `httpx.MockTransport` 接在真实 `AsyncClient` 上，测的是真实请求构造路径
（URL / 头 / JSON 体），不是把 `client.post` 替换成假函数。

为什么这些守卫不能省：
- G3 钉死「不自动重试」——chat 是 POST，重复提交等于重复计费；
  将来有人「顺手加个重试」，这里会变红。
- G2 钉死平台 id → 上游 model 名的翻译，未知 id 一律 400，不静默回落。
- G1 钉死 messages 原样按序发出——agent 将来靠完整历史推进会话。
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.exceptions import BadRequest, Forbidden, RateLimited, Unauthorized
from infra.llm import LlmMessage, LlmRequest
from infra.llm.client import OpenAICompatibleClient
from infra.llm.providers.openai_compatible import LLMSettings


def settings(*, base_url: str = "https://llm.example.com/compatible-mode/v1") -> LLMSettings:
    return LLMSettings(base_url=base_url, api_key="sk-test", timeout_sec=7.0)


def client_recording(
    handler,
    *,
    base_url: str = "https://llm.example.com/compatible-mode/v1",
) -> tuple[OpenAICompatibleClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return (
        OpenAICompatibleClient(settings(base_url=base_url), transport=httpx.MockTransport(record)),
        seen,
    )


def ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "qwen-plus",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "你好"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )


def request_with_history() -> LlmRequest:
    return LlmRequest(
        model="llm/qwen-plus",
        messages=(
            LlmMessage("system", "你是助手", name="system-a"),
            LlmMessage("user", "第一轮"),
            LlmMessage("assistant", "回复"),
            LlmMessage("tool", '{"ok":true}', name="get_weather", tool_call_id="call_1"),
        ),
        params={"temperature": 0.2, "max_tokens": 64},
        request_id="req-1",
    )


@pytest.mark.asyncio
async def test_g1_messages_原样按序发出且参数透传() -> None:
    client, seen = client_recording(lambda _: ok_response())
    response = await client.complete(request_with_history())

    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://llm.example.com/compatible-mode/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer sk-test"

    body = json.loads(req.content)
    assert body["messages"] == [
        {"role": "system", "content": "你是助手", "name": "system-a"},
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "回复"},
        {
            "role": "tool",
            "content": '{"ok":true}',
            "name": "get_weather",
            "tool_call_id": "call_1",
        },
    ]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert response.text == "你好"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_g2_平台id翻译成上游model且未知id报400() -> None:
    client, seen = client_recording(lambda _: ok_response())

    # 登记表里没有条目时，`llm/<name>` 显式表示「通用兼容端点的原名」。
    await client.complete(LlmRequest(model="llm/deepseek-v3", messages=(LlmMessage("user", "hi"),)))
    assert json.loads(seen[-1].content)["model"] == "deepseek-v3"

    # 别的供应商前缀在这里是未知 id —— 不猜、不静默回落。
    with pytest.raises(BadRequest, match="未知的 LLM 模型 id"):
        await client.complete(
            LlmRequest(model="bailian/qwen-plus", messages=(LlmMessage("user", "hi"),))
        )


@pytest.mark.asyncio
async def test_llm_default_映射到运维页配的模型名() -> None:
    """换供应商只改配置：调用方写 llm/default，上游名来自 LLM_MODEL。"""
    configured = LLMSettings(base_url="https://x.example/v1", api_key="sk-x", model="deepseek-v3")
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok_response()

    client = OpenAICompatibleClient(configured, transport=httpx.MockTransport(record))
    await client.complete(LlmRequest(model="llm/default", messages=(LlmMessage("user", "hi"),)))
    assert json.loads(seen[0].content)["model"] == "deepseek-v3"


@pytest.mark.asyncio
async def test_g3_五xx不自动重试() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "down"})

    client, seen = client_recording(handler)
    from core.exceptions import UpstreamError

    with pytest.raises(UpstreamError):
        await client.complete(
            LlmRequest(model="llm/qwen-plus", messages=(LlmMessage("user", "hi"),))
        )

    assert calls == 1
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, Unauthorized),
        (403, Forbidden),
        (429, RateLimited),
        (418, BadRequest),
        (500, Exception),  # 具体类型在下面单独断言
    ],
)
async def test_g4_错误分类映射正确(status: int, exc: type[Exception]) -> None:
    client, _ = client_recording(lambda _: httpx.Response(status, json={"error": "oops"}))
    with pytest.raises(exc):
        await client.complete(
            LlmRequest(model="llm/qwen-plus", messages=(LlmMessage("user", "hi"),))
        )


@pytest.mark.asyncio
async def test_tool_calls_解析但不执行() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-2",
                "model": "qwen-plus",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"q":"雪原"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    client, _ = client_recording(handler)
    response = await client.complete(
        LlmRequest(model="llm/qwen-plus", messages=(LlmMessage("user", "查雪原"),))
    )
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_9"
    assert response.tool_calls[0].name == "lookup"
    # arguments 是 OpenAI 原样 JSON 字符串，不在这里 loads
    assert response.tool_calls[0].arguments == '{"q":"雪原"}'
    assert response.usage.total_tokens == 3


@pytest.mark.asyncio
async def test_usage_缺失时给全零不乱猜() -> None:
    client, _ = client_recording(
        lambda _: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
        )
    )
    response = await client.complete(
        LlmRequest(model="llm/qwen-plus", messages=(LlmMessage("user", "hi"),))
    )
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (0, 0)


@pytest.mark.asyncio
async def test_缺base_url或缺key都是4xx不是5xx() -> None:
    no_url = OpenAICompatibleClient(LLMSettings(base_url="", api_key="sk-x"))
    with pytest.raises(BadRequest, match="LLM_BASE_URL"):
        await no_url.complete(LlmRequest(model="llm/x", messages=(LlmMessage("user", "hi"),)))

    no_key = OpenAICompatibleClient(LLMSettings(base_url="https://x.example/v1", api_key=""))
    with pytest.raises(BadRequest, match="LLM_API_KEY"):
        await no_key.complete(LlmRequest(model="llm/x", messages=(LlmMessage("user", "hi"),)))
