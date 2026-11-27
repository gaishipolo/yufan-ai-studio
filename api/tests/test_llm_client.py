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
import os

import httpx
import pytest

from core.exceptions import BadRequest, Forbidden, RateLimited, Unauthorized, UpstreamError
from infra.llm import LlmMessage, LlmRequest, LlmResponse, LlmUsage
from infra.llm import client as client_module
from infra.llm.client import OpenAICompatibleClient
from infra.llm.client import complete as complete_via_chain
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

    # 直接构造的 client 只认识自己这家的映射；完整入口 complete() 会按 id 前缀
    # 先选供应商再调用（见下面「降级链」用例）。这里钉的是：映射没登记就 400。
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
async def test_上游408保留原始状态码供降级判定() -> None:
    """UpstreamError 会抹平 408/5xx；降级链要靠 upstream_status 区分。"""
    client, _ = client_recording(lambda _: httpx.Response(408, json={"error": "timeout"}))
    with pytest.raises(UpstreamError) as caught:
        await client.complete(
            LlmRequest(model="llm/qwen-plus", messages=(LlmMessage("user", "hi"),))
        )
    assert caught.value.upstream_status == 408


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


# --------------------------------------------------------------------------- #
# 默认模型/降级链：只对 llm/default 生效，且只对「明确失败」降级。
# --------------------------------------------------------------------------- #
class _FakeClient:
    """替换 OpenAICompatibleClient：按 base_url 决定成功还是抛错。"""

    behavior: dict[str, BaseException | None] = {}
    calls: list[str] = []
    base_urls: list[str] = []

    def __init__(
        self,
        settings: LLMSettings,
        *,
        model_map: dict[str, str] | None = None,
        api_key_env: str = "",
        vendor: str = "",
    ) -> None:
        self.base_url = settings.base_url
        self.model_map = model_map or {}

    async def complete(self, request: LlmRequest) -> LlmResponse:
        _FakeClient.calls.append(request.model)
        _FakeClient.base_urls.append(self.base_url)
        outcome = _FakeClient.behavior.get(self.base_url)
        if isinstance(outcome, BaseException):
            raise outcome
        return LlmResponse(
            text="ok",
            model=request.model,
            finish_reason="stop",
            usage=LlmUsage(1, 1, 2),
            platform_model=request.model,
        )


@pytest.fixture
def llm_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """隔离 LLM_* 环境与 .env，并替换掉真实客户端。"""
    import infra.env_settings as env_settings
    import infra.llm.providers.source as source

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(source, "_ENV_PATH", env_file)
    for key in list(os.environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_MODEL_CHAIN", "[]")
    monkeypatch.setattr(client_module, "OpenAICompatibleClient", _FakeClient)
    env_settings.clear_cache()
    _FakeClient.behavior = {}
    _FakeClient.calls = []
    _FakeClient.base_urls = []
    yield
    env_settings.clear_cache()


def _set_provider(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    base_url: str,
    models: str,
) -> None:
    prefix = "LLM" if name == "default" else f"LLM_{name.upper()}"
    monkeypatch.setenv(f"{prefix}_BASE_URL", base_url)
    monkeypatch.setenv(f"{prefix}_API_KEY", "sk-test")
    monkeypatch.setenv(f"{prefix}_MODELS", models)


async def _ask_default() -> LlmResponse:
    return await complete_via_chain(
        LlmRequest(model="llm/default", messages=(LlmMessage("user", "hi"),))
    )


@pytest.mark.asyncio
async def test_默认模型_429时按链降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    _FakeClient.behavior = {"https://minimax.example/v1": RateLimited("busy")}

    response = await _ask_default()
    assert response.platform_model == "llm/qwen"
    assert _FakeClient.calls == ["minimax/flash", "llm/qwen"]
    assert _FakeClient.base_urls == [
        "https://minimax.example/v1",
        "https://default.example/v1",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        Unauthorized("bad key"),
        Forbidden("denied"),
        UpstreamError("upstream 503"),
        httpx.ConnectTimeout("connect timeout"),
    ],
    ids=["401", "403", "5xx", "connect-timeout"],
)
async def test_默认模型_其它明确失败也会降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None, outcome: BaseException
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    if isinstance(outcome, UpstreamError):
        outcome.upstream_status = 503
    _FakeClient.behavior = {"https://minimax.example/v1": outcome}

    response = await _ask_default()
    assert response.platform_model == "llm/qwen"
    assert _FakeClient.calls == ["minimax/flash", "llm/qwen"]


@pytest.mark.asyncio
async def test_默认模型_400不降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    _FakeClient.behavior = {"https://minimax.example/v1": BadRequest("bad params")}

    with pytest.raises(BadRequest):
        await _ask_default()
    assert _FakeClient.calls == ["minimax/flash"]


@pytest.mark.asyncio
async def test_默认模型_读超时不降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    """读超时 = 请求已发出，上游可能已受理；降级会重复计费。"""
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    _FakeClient.behavior = {"https://minimax.example/v1": httpx.ReadTimeout("slow")}

    with pytest.raises(httpx.ReadTimeout):
        await _ask_default()
    assert _FakeClient.calls == ["minimax/flash"]


@pytest.mark.asyncio
async def test_默认模型_408不降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    timeout = UpstreamError("upstream timeout")
    timeout.upstream_status = 408
    _FakeClient.behavior = {"https://minimax.example/v1": timeout}

    with pytest.raises(UpstreamError):
        await _ask_default()
    assert _FakeClient.calls == ["minimax/flash"]


@pytest.mark.asyncio
async def test_默认模型_连接失败会降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    _FakeClient.behavior = {"https://minimax.example/v1": httpx.ConnectError("refused")}

    response = await _ask_default()
    assert response.platform_model == "llm/qwen"
    assert _FakeClient.calls == ["minimax/flash", "llm/qwen"]


@pytest.mark.asyncio
async def test_默认模型_整条链失败时抛最后一个错误(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    _FakeClient.behavior = {
        "https://minimax.example/v1": RateLimited("a"),
        "https://default.example/v1": RateLimited("b"),
    }

    with pytest.raises(RateLimited, match="b"):
        await _ask_default()
    assert _FakeClient.calls == ["minimax/flash", "llm/qwen"]


@pytest.mark.asyncio
async def test_显式模型不走降级(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )
    monkeypatch.setenv("LLM_MODEL_CHAIN", '["minimax/flash","llm/qwen"]')
    _FakeClient.behavior = {"https://minimax.example/v1": RateLimited("busy")}

    with pytest.raises(RateLimited):
        await complete_via_chain(
            LlmRequest(model="minimax/flash", messages=(LlmMessage("user", "hi"),))
        )
    assert _FakeClient.calls == ["minimax/flash"]
    assert _FakeClient.base_urls == ["https://minimax.example/v1"]


@pytest.mark.asyncio
async def test_具名供应商路由到自己的网关(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        '[{"id":"minimax/flash","upstream":"MiniMax"}]',
    )

    response = await complete_via_chain(
        LlmRequest(model="minimax/flash", messages=(LlmMessage("user", "hi"),))
    )
    assert response.platform_model == "minimax/flash"
    assert _FakeClient.base_urls == ["https://minimax.example/v1"]


@pytest.mark.asyncio
async def test_链为空时llm_default退回旧语义(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    _set_provider(
        monkeypatch,
        "default",
        "https://default.example/v1",
        '[{"id":"llm/qwen","upstream":"qwen"}]',
    )

    response = await _ask_default()
    assert response.platform_model == "llm/default"
    assert _FakeClient.calls == ["llm/default"]


@pytest.mark.asyncio
async def test_具名供应商未配置时是400而不是静默回落(
    llm_runtime: None,
) -> None:
    with pytest.raises(BadRequest, match="LLM_MINIMAX_BASE_URL"):
        await complete_via_chain(
            LlmRequest(model="minimax/flash", messages=(LlmMessage("user", "hi"),))
        )
    assert _FakeClient.calls == []


def test_provider_of与load_provider按前缀取配置(
    monkeypatch: pytest.MonkeyPatch, llm_runtime: None
) -> None:
    from infra.llm.providers.openai_compatible import load_provider, resolve_model
    from infra.llm.providers.source import provider_of

    _set_provider(
        monkeypatch,
        "minimax",
        "https://minimax.example/v1",
        # 手工/环境变量配置的旧形状：具名供应商仍写 llm/*；运行时也要迁到前缀下。
        '[{"id":"llm/flash","upstream":"MiniMax"}]',
    )
    assert provider_of("llm/qwen") == "default"
    assert provider_of("minimax/flash") == "minimax"

    runtime = load_provider("minimax")
    assert runtime.settings.base_url == "https://minimax.example/v1"
    assert runtime.api_key_env == "LLM_MINIMAX_API_KEY"
    assert resolve_model("minimax/flash", runtime.settings, runtime.model_map) == "MiniMax"
