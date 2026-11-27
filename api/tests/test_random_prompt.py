"""随机档提示词：整段替换主提示词、没配就拒绝、引用走同一套规则。

走 HTTP 而不是直接调 service：`useRandomPrompt` 是使用者那一侧的选择，
序列化别名（`randomPrompt` / `useRandomPrompt`）错了，纯 service 测试看不出来。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from test_template_spec_api import API, _draft, _headers, _media, _save


def _publish(client: TestClient, headers: dict[str, str], draft: dict) -> None:
    response = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert response.status_code == 200, response.text


def test_random_prompt_is_exposed_in_the_out(client: TestClient) -> None:
    """前端判「随机档可不可选」只看接口里的 `latest.randomPrompt`。

    这条钉的是「**存了要能读出来**」：出参构造里漏映射时，库里和草稿里都有值，
    接口却永远返回空串 —— 模板明明配了、发布了，档位还是灰的，而且看不出为什么。
    只测 compile 抓不到这个（compile 直接读 ORM 行，不经过出参）。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, randomPrompt="夜景版：换成 {{slot:hero}}")
    _save(client, headers, draft)
    _publish(client, headers, draft)

    body = client.get(f"{API}/{draft['key']}", headers=headers)
    assert body.status_code == 200, body.text
    latest = body.json()["data"]["latest"]
    assert latest["randomPrompt"] == "夜景版：换成 {{slot:hero}}"

    # 列表接口走同一个 `_spec_out`，也顺带确认一遍（前端用的是列表）
    listed = client.get(API, params={"limit": 50}, headers=headers).json()["data"]["items"]
    mine = next(item for item in listed if item["key"] == draft["key"])
    assert mine["latest"]["randomPrompt"] == "夜景版：换成 {{slot:hero}}"


def test_random_prompt_replaces_the_whole_prompt(client: TestClient) -> None:
    """随机档是**整段替换**，不是追加：主提示词的段落一个字都不该留下。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(
        video,
        first,
        second,
        randomPrompt="随机档：{{slot:hero}} 换成夜景，镜头缓慢推近",
    )
    _save(client, headers, draft)
    _publish(client, headers, draft)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    prompt = body.json()["data"]["prompt"]
    assert prompt.startswith("随机档：")
    assert "镜头缓慢推近" in prompt
    # 主提示词那句不该混进来 —— 混进来就说明是「追加」而不是「替换」
    assert "8 秒 · 9:16 竖屏" not in prompt


def test_without_the_flag_the_main_prompt_is_used(client: TestClient) -> None:
    """均衡 / 自定义档不传这个标记，老调用方零影响。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, randomPrompt="随机档：完全不同的说法")
    _save(client, headers, draft)
    _publish(client, headers, draft)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    assert body.json()["data"]["prompt"].startswith("8 秒 · 9:16 竖屏")


def test_asking_for_random_prompt_without_config_is_rejected(client: TestClient) -> None:
    """没配就 400，**不静默回退主提示词**：回退的样子是「随机档和均衡档产出一模一样」。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)  # 没写 randomPrompt
    _save(client, headers, draft)
    _publish(client, headers, draft)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "useRandomPrompt": True},
        headers=headers,
    )
    assert body.status_code == 400
    assert "未配置随机档提示词" in body.json()["message"]


def test_random_prompt_referencing_unknown_slot_is_rejected_on_save(
    client: TestClient,
) -> None:
    """与主提示词同一套规则：引用不存在的槽位在**保存**时就报错。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, randomPrompt="随机档：{{slot:nope}} 换个说法")

    response = client.post(API, json=draft, headers=headers)
    assert response.status_code == 400
    assert "随机档提示词" in response.json()["message"]


def test_random_prompt_optional_slot_needs_fallback_at_publish(client: TestClient) -> None:
    """随机档不传可选素材，所以它**单张引用**可选槽位时同样要写兜底文案。

    只在主提示词上查会放行这种模板，使用者切到随机档那一次才被 400 打回 ——
    而那时他既不知道原因，也不知道该找作者改什么。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(
        video,
        first,
        second,
        # 主提示词里**不引用** note —— 只让随机档那一段引用它，这样发布时拦下来
        # 的就只能是随机档那条（主提示词那条没有触发条件）。
        editable="主图 {{slot:hero}}；原片 {{slot:source}}",
        randomPrompt="随机档：{{slot:hero}} 与 {{slot:note}} 换夜景",
    )
    # note 是可选槽位且没写兜底文案 → 随机档单张引用它 → 发布时要拦。
    draft["slots"][2]["emptyText"] = ""
    draft["slots"][2]["emptyTextFromAnchor"] = False
    _save(client, headers, draft)

    response = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert response.status_code == 400
    assert "未选时文案" in response.json()["message"]
