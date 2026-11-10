"""Agnes 驱动：请求形状、错误分类，以及是否符合驱动开发标准。

## 为什么钉「请求形状」

官方文档明确要求 `response_format` 在 `extra_body` 内、图生图图片走
`extra_body.image`、不要带 `tags: ["img2img"]`。这三条错了都只在线上报错，
所以用 `httpx.MockTransport` 接在真实 `AsyncClient` 上，测真实请求构造路径
（不是 mock 掉 `submit` 内部函数——那样会把「其实没发对字段」测成绿的）。

## 为什么钉「标准符合性」

agnes 原本挂在 `AI_PROVIDERS` 上（`infra/ai/agnes.py`）。挪进 `drivers/` 后
它**不再出现在配置里**，如果自动载入哪天退化了，症状是 `GET /models` 里
突然没有 agnes，而不是启动失败。所以这里直接断言：发现得到、校验通过、
路由解析得到、仍是默认生图模型。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from infra.ai.base import Capability, GenerationRequest, GenerationState
from modules.model_driver.drivers import agnes as agnes_module
from modules.model_driver.drivers.agnes import AgnesDriver, AgnesSettings


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 AsyncClient 换成 MockTransport，记录真实发出的 URL / 请求体。"""
    seen: dict[str, Any] = {}
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["payload"] = json.loads(request.content)
            seen["authorization"] = request.headers.get("authorization", "")
            return httpx.Response(
                200,
                json={
                    "created": 1794319801,
                    "task_id": "task_ABC123",
                    "data": [
                        {
                            "url": "https://cos.example.com/images/i2i/task_ABC123/output.png",
                            "b64_json": "",
                            "revised_prompt": "",
                        }
                    ],
                },
            )

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("AGNES_API_KEY", "sk-test-key")
    monkeypatch.setenv("AGNES_BASE_URL", "https://agnes.example")
    monkeypatch.setattr(agnes_module.httpx, "AsyncClient", factory)
    return seen


def _request(**overrides: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "capability": Capability.IMAGE,
        "model": "agnes-image-2.5-flash",
        "prompt": "a white cup on a wooden table",
        "params": {"size": "1024x768"},
    }
    base.update(overrides)
    return GenerationRequest(**base)


# ------------------------------------------------------------------ 请求形状
async def test_text_to_image_keeps_response_format_inside_extra_body(
    captured: dict[str, Any],
) -> None:
    await AgnesDriver().submit(_request())

    payload = captured["payload"]
    assert payload["model"] == "agnes-image-2.5-flash"
    assert payload["size"] == "1024x768"
    assert payload["extra_body"]["response_format"] == "url"
    assert "response_format" not in payload, "放顶层会被上游拒绝"
    assert "image" not in payload["extra_body"], "文生图不能带 image"
    assert "tags" not in payload, "文档明确说不需要 tags: [img2img]"
    assert "n" not in payload, "n 只允许 1，传了反而多一个被拒的机会"


async def test_image_to_image_sends_references_in_extra_body(
    captured: dict[str, Any],
) -> None:
    await AgnesDriver().submit(
        _request(
            reference_urls=(
                "https://example.com/a.png",
                "https://example.com/b.png",
            )
        )
    )

    assert captured["payload"]["extra_body"]["image"] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


async def test_reference_order_is_preserved_and_deduped(
    captured: dict[str, Any],
) -> None:
    """多图合成靠顺序指代「第一张/第二张」，重排会让提示词与图片错位。"""
    await AgnesDriver().submit(
        _request(
            reference_urls=(
                "https://example.com/char-2.png",
                "",
                "https://example.com/char-1.png",
                "https://example.com/char-2.png",
            )
        )
    )

    assert captured["payload"]["extra_body"]["image"] == [
        "https://example.com/char-2.png",
        "https://example.com/char-1.png",
    ]


async def test_data_uri_reference_passes_through(captured: dict[str, Any]) -> None:
    """文档允许 Data URI；驱动不替调用方做隐式下载。"""
    data_uri = "data:image/png;base64,BASE64_HERE"
    await AgnesDriver().submit(_request(reference_urls=(data_uri,)))

    assert captured["payload"]["extra_body"]["image"] == [data_uri]


async def test_success_maps_url_and_task_id(captured: dict[str, Any]) -> None:
    progress = await AgnesDriver().submit(_request())

    assert progress.state is GenerationState.SUCCEEDED
    assert progress.progress == 100
    assert progress.external_id == "task_ABC123", "任务号要落库，用于账单对账"
    assert progress.output_url and progress.output_url.endswith("output.png")
    assert progress.cover_url == progress.output_url
    assert captured["url"] == "https://agnes.example/v1/images/generations"
    assert captured["authorization"] == "Bearer sk-test-key"


# ------------------------------------------------------------------ 错误分类
async def test_model_404_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    """「当前分组不支持模型」是永久错误，归 BadRequest 才不会被重试三次。"""
    from core.exceptions import BadRequest

    _fail_with(
        monkeypatch,
        404,
        {
            "error": {
                "code": "model_not_found",
                "message": "当前分组不支持模型 agnes-image-2.0-flash",
            }
        },
    )

    with pytest.raises(BadRequest) as exc:
        await AgnesDriver().submit(_request(model="agnes-image-2.0-flash"))

    assert exc.value.retryable is False
    assert "分组" in str(exc.value), "要把上游原因原样带给用户，否则只能猜"


async def test_base64_only_response_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """只要 url 却只拿到 Base64：明确失败，不能把几 MB 的 Base64 塞进 output_url。"""
    from core.exceptions import UpstreamError

    _respond_with(
        monkeypatch,
        {
            "created": 1,
            "data": [{"url": None, "b64_json": "iVBORw0KGgo" * 1000}],
        },
    )

    with pytest.raises(UpstreamError) as exc:
        await AgnesDriver().submit(_request())

    assert "Base64" in str(exc.value)
    assert "response_format" in str(exc.value), "要指向真正的原因，否则没法排查"


async def test_empty_data_fails_with_body_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 但没有图（多是审核未通过）：报错要带上响应片段。"""
    from core.exceptions import UpstreamError

    _respond_with(monkeypatch, {"created": 1, "data": []})

    with pytest.raises(UpstreamError) as exc:
        await AgnesDriver().submit(_request())

    assert "未返回图片地址" in str(exc.value)
    assert "created" in str(exc.value)


async def test_missing_api_key_fails_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.exceptions import UpstreamError

    monkeypatch.setenv("AGNES_API_KEY", "  ")

    with pytest.raises(UpstreamError) as exc:
        await AgnesDriver().submit(_request())

    assert "AGNES_API_KEY" in str(exc.value)


async def test_count_above_one_is_rejected_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`model_driver` 那条路不校验 param_schema，这道检查是它唯一的护栏。

    少了它，`params={"count": 4}` 会被上游静默裁成 1 张：用户以为拿到 4 张，
    账单只有 1 张，而且全程没有一处报错。
    """
    from core.exceptions import BadRequest

    def _no_network(**kwargs: Any) -> httpx.AsyncClient:  # pragma: no cover
        raise AssertionError("张数非法时不该发出请求")

    monkeypatch.setenv("AGNES_API_KEY", "sk-test-key")
    monkeypatch.setattr(agnes_module.httpx, "AsyncClient", _no_network)

    with pytest.raises(BadRequest) as exc:
        await AgnesDriver().submit(_request(params={"size": "1024x1024", "count": 4}))

    assert "1 张" in str(exc.value)


async def test_count_of_one_still_passes(captured: dict[str, Any]) -> None:
    progress = await AgnesDriver().submit(_request(params={"size": "1024x1024", "count": 1}))
    assert progress.state is GenerationState.SUCCEEDED
    assert "n" not in captured["payload"]


# -------------------------------------------------------------- 标准符合性
def test_driver_passes_contract_validation() -> None:
    from modules.model_driver.spec import validate_driver

    assert validate_driver(AgnesDriver()) == []


def test_declared_models_are_the_ones_actually_usable() -> None:
    """2.0-flash 在当前密钥分组是 404，声明它等于把「选错模型」藏到异步失败里。"""
    ids = [model.id for model in AgnesDriver.models]
    assert ids == ["agnes-image-2.5-flash", "agnes-image-2.1-flash"]
    for model in AgnesDriver.models:
        assert model.provider == "agnes"
        assert model.capability is Capability.IMAGE


def test_driver_is_auto_loaded_without_env_listing(client: Any) -> None:
    """核心诉求：不进 `AI_PROVIDERS` 也要被发现、校验、接进路由。"""
    from core.config import settings
    from infra import get_model_router
    from modules.model_driver.registry import driver_summaries, load_errors

    assert "agnes" not in settings.ai_provider_list, "它不该再靠配置清单加载"
    assert not load_errors(), load_errors()

    infos = {item.name: item for item in driver_summaries()}
    assert "agnes" in infos
    assert infos["agnes"].loaded is True
    assert infos["agnes"].model_count == 2

    router = get_model_router()
    assert router.resolve("agnes-image-2.5-flash") is not None
    assert router.provider_for("agnes-image-2.5-flash").name == "agnes"


def test_agnes_stays_the_default_image_model(client: Any) -> None:
    """默认模型不能由注册顺序决定：驱动永远后于 `AI_PROVIDERS` 注册。

    没有 `preferred_for` 的话，把 agnes 挪进 drivers/ 会让默认生图模型
    悄悄变成 echo 的 seedream-3——`generate` 不传 model 时就去打假供应商。
    """
    from infra import get_model_router

    assert get_model_router().default_model(Capability.IMAGE) == "agnes-image-2.5-flash"


def test_default_gateway_stays_on_cn_domain() -> None:
    """文档写的 apihub 要另一把密钥；默认值改过去会让线上直接 401。"""
    assert AgnesSettings().base_url == "https://api.agnes-ai.cn"


def _respond_with(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
            **kwargs,
        )

    monkeypatch.setenv("AGNES_API_KEY", "sk-test-key")
    monkeypatch.setattr(agnes_module.httpx, "AsyncClient", factory)


def _fail_with(monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]) -> None:
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)),
            **kwargs,
        )

    monkeypatch.setenv("AGNES_API_KEY", "sk-test-key")
    monkeypatch.setattr(agnes_module.httpx, "AsyncClient", factory)
