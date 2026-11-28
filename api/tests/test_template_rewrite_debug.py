"""LLM 调试台：选模板、改 system prompt/参数、看改写与最终提示词；不建任务。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from library_support import admin_headers, make_user
from test_template_spec_api import API, _draft, _headers, _media, _save

from core.exceptions import BadRequest
from core.template_spec import normalize_refs
from infra.llm import LlmMessage, LlmRequest, LlmResponse, LlmUsage
from modules.template_pack import rewrite_debug, spec_service
from modules.template_pack.randomness import RewriteTrace


@pytest.fixture(autouse=True)
def _reset_random_settings() -> Iterator[None]:
    """配置写进 app_meta 是全局状态；每个用例后清掉，别污染其它文件。"""
    yield
    import asyncio

    from sqlalchemy import delete

    from db.app_meta import AppMeta
    from db.session import get_session_factory
    from modules.template_pack.random_settings import RANDOM_REWRITE_KEY

    async def clean() -> None:
        async with get_session_factory()() as session:
            await session.execute(delete(AppMeta).where(AppMeta.key == RANDOM_REWRITE_KEY))
            await session.commit()

    asyncio.run(clean())


def _publish(client: TestClient, headers: dict[str, str], draft: dict) -> None:
    response = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert response.status_code == 200, response.text


def _debug_headers(client: TestClient) -> dict[str, str]:
    """admin 没有 llm_config:write；调试台用它自己的权限开一个用户。"""
    _, headers = make_user(client, admin_headers(client), ["llm_config:write"])
    return headers


def _trace(
    spec: Any,
    *,
    text: str,
    params: dict[str, Any] | None = None,
    model: str = "llm/default",
) -> RewriteTrace:
    request = LlmRequest(
        model=model,
        messages=(
            LlmMessage("system", "调试 system"),
            LlmMessage("user", '{"slots":[]}'),
        ),
        params=params or {"temperature": 0.7, "max_tokens": 123},
    )
    response = LlmResponse(
        text=text,
        model="qwen-plus",
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=9, completion_tokens=5, total_tokens=14),
        platform_model="primary/qwen-plus",
    )
    return RewriteTrace(
        spec=normalize_refs(spec),
        request=request,
        response=response,
        overrides={"note": "雨夜的霓虹街道"},
        latency_ms=12,
    )


def test_debug_options_lists_templates_and_defaults(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    body = client.get(f"{API}/debug/templates", headers=_debug_headers(client))
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["defaultTemperature"] == 1.0
    assert "提示词改写器" in data["defaultSystemPrompt"]
    assert data["maxSourceChars"] == 6000
    assert data["customized"] is False
    assert data["currentSettings"] == {
        "model": "llm/default",
        "systemPrompt": data["defaultSystemPrompt"],
        "temperature": 1.0,
        "maxTokens": 8000,
        "extraParams": {},
    }
    item = next(item for item in data["templates"] if item["key"] == draft["key"])
    assert item["version"] == 1
    assert item["status"] == "published"


def test_debug_rewrite_returns_raw_and_compiled_prompt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    async def fake(spec: Any, **kwargs: Any) -> RewriteTrace:
        settings = kwargs["settings"]
        assert settings.model == "primary/qwen-plus"
        assert settings.system_prompt == "自定义 system"
        assert settings.temperature == 0.3
        assert settings.max_tokens == 456
        return _trace(
            spec,
            text=json.dumps({"slots": [{"key": "note", "text": "雨夜的霓虹街道"}]}),
            model=settings.model,
        )

    monkeypatch.setattr(rewrite_debug, "rewrite_slot_texts_trace", fake)

    body = client.post(
        f"{API}/debug/rewrite",
        headers=_debug_headers(client),
        json={
            "templateKey": draft["key"],
            "model": "primary/qwen-plus",
            "systemPrompt": "自定义 system",
            "temperature": 0.3,
            "maxTokens": 456,
            "extraParams": {"top_p": 0.9},
        },
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["ok"] is True
    assert data["error"] == ""
    assert data["requestedModel"] == "primary/qwen-plus"
    assert data["systemPrompt"] == "调试 system"
    assert data["userPayload"] == '{"slots":[]}'
    assert data["requestParams"]["temperature"] == 0.7
    assert data["rawOutput"].startswith('{"slots"')
    assert data["rewrittenTexts"] == {"note": "雨夜的霓虹街道"}
    assert "雨夜的霓虹街道" in data["finalPrompt"]
    assert data["promptLength"] == len(data["finalPrompt"])
    assert data["promptLimit"] == 7000
    assert data["syntheticSlots"] == ["hero"]
    assert data["usage"]["totalTokens"] == 14


def test_debug_rewrite_returns_raw_output_even_when_validation_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    async def fake(spec: Any, **kwargs: Any) -> RewriteTrace:
        trace = _trace(spec, text='{"slots": [{"key": "note", "text": "雨夜"}]}')
        return RewriteTrace(
            spec=trace.spec,
            request=trace.request,
            response=trace.response,
            overrides={},
            latency_ms=8,
            error=BadRequest("模型改写出来的结果格式不对：不是 JSON"),
        )

    monkeypatch.setattr(rewrite_debug, "rewrite_slot_texts_trace", fake)

    body = client.post(
        f"{API}/debug/rewrite",
        headers=_debug_headers(client),
        json={"templateKey": draft["key"], "systemPrompt": "s"},
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["ok"] is False
    assert "不是 JSON" in data["error"]
    assert data["rawOutput"] == '{"slots": [{"key": "note", "text": "雨夜"}]}'
    assert data["finalPrompt"] == ""


def test_debug_rewrite_without_rewritable_slots_reports_error(
    client: TestClient,
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(
        video,
        first,
        second,
        editable="{{slot:hero}}；原片 {{slot:source}}",
    )
    _save(client, headers, draft)
    _publish(client, headers, draft)

    body = client.post(
        f"{API}/debug/rewrite",
        headers=_debug_headers(client),
        json={"templateKey": draft["key"], "systemPrompt": "s"},
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["ok"] is False
    assert "没有可随机的槽位" in data["error"]
    assert data["rawOutput"] == ""


def test_save_settings_overrides_production_random_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    debug = _debug_headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    saved = client.put(
        f"{API}/debug/settings",
        headers=debug,
        json={
            "model": "primary/qwen-plus",
            "systemPrompt": "生产用的新 system",
            "temperature": 0.4,
            "maxTokens": 789,
            "extraParams": {"top_p": 0.8},
        },
    )
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["model"] == "primary/qwen-plus"
    assert data["systemPrompt"] == "生产用的新 system"
    assert data["temperature"] == 0.4

    options = client.get(f"{API}/debug/templates", headers=debug).json()["data"]
    assert options["customized"] is True
    assert options["currentSettings"]["model"] == "primary/qwen-plus"
    assert options["currentSettings"]["systemPrompt"] == "生产用的新 system"

    seen: list[Any] = []

    async def fake_rewrite(spec: Any, **kwargs: Any) -> dict[str, str]:
        seen.append(kwargs["settings"])
        return {"note": "雨夜"}

    monkeypatch.setattr(spec_service, "rewrite_slot_texts", fake_rewrite)
    compiled = client.post(
        f"{API}/{draft['key']}/compile",
        headers=headers,
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
    )
    assert compiled.status_code == 200, compiled.text
    assert seen[0].model == "primary/qwen-plus"
    assert seen[0].system_prompt == "生产用的新 system"
    assert seen[0].temperature == 0.4
    assert seen[0].max_tokens == 789
    assert seen[0].extra_params == {"top_p": 0.8}
