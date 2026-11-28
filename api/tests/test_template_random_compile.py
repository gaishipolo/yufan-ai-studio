"""随机档编译：模型改写兜底文案，且不再碰 `random_prompt`。

走 HTTP：`useRandomPrompt` / 序列化别名出错的路径，纯 service 测试看不出来。
模型调用用猴子补丁替换，不碰网络。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_template_spec_api import API, _draft, _headers, _media, _save

from core.exceptions import Unauthorized
from modules.template_pack import spec_service
from modules.template_pack.randomness import rewrite_slot_texts as real_rewrite


def _publish(client: TestClient, headers: dict[str, str], draft: dict) -> None:
    response = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert response.status_code == 200, response.text


def _patch_rewrite(
    monkeypatch: pytest.MonkeyPatch,
    *,
    texts: dict[str, str] | None = None,
) -> list[Any]:
    calls: list[Any] = []

    async def fake(spec: Any, **_: Any) -> dict[str, str]:
        calls.append(spec)
        return texts or {"note": "雨夜的霓虹街道"}

    monkeypatch.setattr(spec_service, "rewrite_slot_texts", fake)
    return calls


def test_random_uses_model_rewrite_and_main_prompt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, randomPrompt="随机档：完全不同的说法")
    _save(client, headers, draft)
    _publish(client, headers, draft)
    calls = _patch_rewrite(monkeypatch)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    prompt = body.json()["data"]["prompt"]
    assert prompt.startswith("8 秒 · 9:16 竖屏")
    assert "雨夜的霓虹街道" in prompt
    # random_prompt 不再参与编译：作者那段话一个字都不该出现
    assert "随机档：完全不同的说法" not in prompt
    assert len(calls) == 1
    assert calls[0].prompt.startswith("8 秒 · 9:16 竖屏")


def test_random_rejects_slot_text_overrides_before_calling_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)
    calls = _patch_rewrite(monkeypatch)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={
            "slotValues": {"hero": [first]},
            "useRandomPrompt": True,
            "slotTextOverrides": {"note": "自己写一句"},
        },
        headers=headers,
    )
    assert body.status_code == 400
    assert "不能同时提交改写" in body.json()["message"]
    assert calls == []


def test_random_rejects_optional_asset_values_before_calling_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)
    calls = _patch_rewrite(monkeypatch)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={
            "slotValues": {"hero": [first], "note": [second]},
            "useRandomPrompt": True,
        },
        headers=headers,
    )
    assert body.status_code == 400
    assert "随机档不传可选槽位素材" in body.json()["message"]
    assert calls == []


def test_random_without_rewritable_slots_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    # note 不在提示词里 → 没有插入点 → 没有可改写槽位
    draft = _draft(
        video,
        first,
        second,
        editable="{{slot:hero}}；原片 {{slot:source}}",
    )
    _save(client, headers, draft)
    _publish(client, headers, draft)
    calls = _patch_rewrite(monkeypatch)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
        headers=headers,
    )
    assert body.status_code == 400
    assert "没有可随机的槽位" in body.json()["message"]
    assert calls == []


def test_random_upstream_401_does_not_leak_as_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    async def failing(spec: Any, **kwargs: Any) -> dict[str, str]:
        async def completer(request: Any, *, timeout_sec: Any = None, deadline: Any = None) -> Any:
            raise Unauthorized("bad key")

        return await real_rewrite(spec, complete=completer, settings=kwargs.get("settings"))

    monkeypatch.setattr(spec_service, "rewrite_slot_texts", failing)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
        headers=headers,
    )
    assert body.status_code == 502
    assert "认证失败" in body.json()["message"]


def test_random_ends_read_transaction_before_calling_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    events: list[str] = []
    original_commit = AsyncSession.commit
    original_fills = spec_service._fills

    async def commit(self: AsyncSession) -> None:
        events.append("commit")
        await original_commit(self)

    async def fills(*args: Any, **kwargs: Any) -> Any:
        events.append("fills")
        return await original_fills(*args, **kwargs)

    async def rewrite(spec: Any, **_: Any) -> dict[str, str]:
        events.append("llm")
        return {"note": "雨夜的霓虹街道"}

    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr(spec_service, "_fills", fills)
    monkeypatch.setattr(spec_service, "rewrite_slot_texts", rewrite)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    assert events.index("fills") < events.index("commit") < events.index("llm")


def test_dead_random_prompt_is_not_validated_inlined_or_published(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧字段里的怪引用 / 修饰符不该再挡住保存与发布，也不该被内联或写进新版本。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    raw = "随机档：{{slot:nope}} {{Mixed 2*}} 都别管"
    draft = _draft(video, first, second, randomPrompt=raw)
    _save(client, headers, draft)
    _publish(client, headers, draft)

    latest = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]["latest"]
    assert latest["randomPrompt"] == ""
    assert "{{slot:nope}}" not in latest["prompt"]


def test_editing_only_random_prompt_does_not_create_a_version(
    client: TestClient,
) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, randomPrompt="第一版随机文案")
    _save(client, headers, draft)
    _publish(client, headers, draft)

    draft["randomPrompt"] = "只改这个死字段"
    _save(client, headers, draft)
    second = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["version"] == 1