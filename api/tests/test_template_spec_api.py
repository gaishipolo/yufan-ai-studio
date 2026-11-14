"""模板规格接口：预检 / 保存幂等 / 发布冻结 / 编译派生编号。

这些用例全部走 HTTP，不直接调 service —— 要验的正是"路由 + 权限 + 序列化"这一层，
而纯规则已经由 `test_template_spec_compile.py` 覆盖。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix
from tests_template_helpers import png_bytes, upload, video_bytes

API = "/api/v1/template-specs"
PERMS = ["template_pack:read", "template_pack:create", "template_pack:publish", *LIB_RW]
#: 真实驱动目录里的模型（`_check_model` 会查目录，不能用假 id）。
MODEL = "libtv-wan3-replacement"


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMS)
    return headers


def _media(client: TestClient, headers: dict[str, str]) -> tuple[str, str, str]:
    video = upload(
        client, f"clip-{suffix()}.mp4", "source", video_bytes(2, audio=True), headers=headers
    )
    first = upload(client, f"a-{suffix()}.png", "image", png_bytes(), headers=headers)
    second = upload(client, f"b-{suffix()}.png", "image", png_bytes(), headers=headers)
    return str(video["id"]), str(first["id"]), str(second["id"])


def _draft(
    video: str,
    first: str,
    second: str,
    *,
    key: str | None = None,
    editable: str | None = None,
    **over: Any,
) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "key": key or f"spec-{suffix()}",
        "name": "演示规格",
        "model": MODEL,
        "params": {},
        "prompt": {
            "locked": "8 秒 · 9:16 竖屏",
            "editable": editable
            or "{{slot:hero}} 与 {{slot:note*}}；原片 {{slot:source}}",
        },
        "slots": [
            {
                "key": "source",
                "type": "video",
                "binding": "fixed",
                "assetId": video,
                "position": 0,
                "label": "原片",
            },
            {
                "key": "hero",
                "type": "image",
                "binding": "user",
                "minCount": 1,
                "maxCount": 1,
                "position": 1,
                "label": "主图",
            },
            {
                "key": "note",
                "type": "image",
                "binding": "user",
                "minCount": 0,
                "maxCount": 2,
                "position": 2,
                "label": "细节",
            },
        ],
    }
    draft.update(over)
    return draft


def _save(client: TestClient, headers: dict[str, str], draft: dict[str, Any]) -> dict[str, Any]:
    response = client.post(API, json=draft, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_validate_returns_fingerprint(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    body = client.post(f"{API}/validate", json=_draft(video, first, second), headers=headers)
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["valid"] is True
    assert len(data["fingerprint"]) == 64


def test_validate_rejects_hardcoded_numbers(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, editable="{{Mixed 2}} 替换成 {{slot:hero}}")
    body = client.post(f"{API}/validate", json=draft, headers=headers)
    assert body.status_code == 400
    assert "不要直接写" in body.json()["message"]


def test_validate_rejects_unknown_model_param(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, params={"不存在的参数": 1})
    body = client.post(f"{API}/validate", json=draft, headers=headers)
    assert body.status_code == 400
    assert "不支持参数" in body.json()["message"]


def test_save_is_idempotent_and_guards_revision(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)

    saved = _save(client, headers, draft)
    assert saved["revision"] == 1
    assert saved["status"] == "draft"
    assert saved["draft"]["key"] == draft["key"]

    # 同内容重发：不加修订号（否则每次自动保存都会把 revision 顶上去）。
    again = _save(client, headers, draft)
    assert again["revision"] == 1

    # 改了内容但带着过期 revision：409，提示刷新。
    stale = dict(draft, name="改名", revision=0)
    conflict = client.post(API, json=stale, headers=headers)
    assert conflict.status_code == 409
    assert "请刷新" in conflict.json()["message"]


def test_publish_freezes_versions_and_repeats_idempotently(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)

    first_publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert first_publish.status_code == 200, first_publish.text
    assert first_publish.json()["data"]["version"] == 1

    # 同一份草稿再发：版本号不动（回执要能重复调）。
    repeat = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert repeat.json()["data"]["version"] == 1

    # 改草稿再发：新版本，旧版本内容必须保持发布那一刻。
    changed = dict(draft, prompt={"locked": "8 秒 · 9:16 竖屏", "editable": "{{slot:hero}} 换掉"})
    _save(client, headers, changed)
    second_publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert second_publish.json()["data"]["version"] == 2

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["currentVersion"] == 2
    assert detail["latest"]["version"] == 2
    assert detail["latest"]["prompt"]["editable"] == "{{slot:hero}} 换掉"
    assert len(detail["latest"]["slots"]) == 3


def test_compile_derives_numbers_and_reference_order(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first], "note": [second]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    # 固定原片是 1 号；user 槽位按 position 依次拿号；`{{slot:note*}}` 展开全部。
    assert data["prompt"].startswith("8 秒 · 9:16 竖屏")
    assert "{{Mixed 2}} 与 {{Mixed 3}}；原片 {{Mixed 1}}" in data["prompt"]
    assert data["referenceLibraryIds"] == [video, first, second]
    assert [item["key"] for item in data["slots"]] == ["source", "hero", "note"]


def test_compile_without_required_slot_is_rejected(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(f"{API}/{draft['key']}/compile", json={"slotValues": {}}, headers=headers)
    assert body.status_code == 400
    assert "至少需要" in body.json()["message"]


def test_compile_expands_library_pack_without_reordering(client: TestClient) -> None:
    """`source=pack` 的槽位由资产库给成员与顺序，编译只照抄。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    pack = client.post(
        "/api/v1/library/packs",
        json={
            "name": f"三视图-{suffix()}",
            "items": [
                {"assetId": second, "role": "multiview"},
                {"assetId": first, "role": "front"},
            ],
        },
        headers=headers,
    )
    assert pack.status_code == 201, pack.text
    pack_id = pack.json()["data"]["id"]

    draft = _draft(video, first, second)
    draft["prompt"]["editable"] = "{{slot:detail*}} 换成 {{slot:source}}"
    draft["slots"] = [
        draft["slots"][0],
        {
            "key": "detail",
            "type": "image",
            "source": "pack",
            "binding": "user",
            "position": 1,
            "label": "细节封装",
        },
    ]
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"detail": [pack_id]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["prompt"].endswith("{{Mixed 2}}、{{Mixed 3}} 换成 {{Mixed 1}}")
    # 顺序 = 封装成员顺序（second → first），不是按 id 排序。
    assert data["referenceLibraryIds"] == [video, second, first]


def test_text_slot_goes_to_text_ids(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    text = upload(client, f"brief-{suffix()}.md", "text", b"# brief\nhello", headers=headers)
    draft = _draft(video, first, second)
    draft["slots"].append(
        {
            "key": "brief",
            "type": "text",
            "binding": "user",
            "refMode": "attachment",
            "minCount": 0,
            "position": 3,
            "label": "文案",
        }
    )
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first], "brief": [str(text["id"])]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["textLibraryIds"] == [str(text["id"])]
    assert str(text["id"]) not in data["referenceLibraryIds"]


def test_disabled_spec_cannot_compile(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    off = client.post(f"{API}/{draft['key']}/disable", headers=headers)
    assert off.status_code == 200, off.text
    assert off.json()["data"]["status"] == "disabled"

    body = client.post(f"{API}/{draft['key']}/compile", json={"slotValues": {}}, headers=headers)
    assert body.status_code == 409
    assert "已停用" in body.json()["message"]


def test_published_list_only_shows_published(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)

    before = client.get(f"{API}/published", headers=headers).json()["data"]
    keys = {item["key"] for item in before["items"]}
    assert draft["key"] not in keys

    client.post(f"{API}/{draft['key']}/publish", headers=headers)
    after = client.get(f"{API}/published", headers=headers).json()["data"]
    assert draft["key"] in {item["key"] for item in after["items"]}


def test_compile_applies_prompt_override(client: TestClient) -> None:
    """填表时改写 `editable` 段：`locked` 段与编号都不受影响。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={
            "slotValues": {"hero": [first]},
            "promptOverride": "把 {{slot:hero}} 换成同款米白色",
        },
        headers=headers,
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    # locked 段原样保留在最前，override 替换了 editable 段，编号仍然派生。
    assert data["prompt"] == "8 秒 · 9:16 竖屏\n\n把 {{Mixed 2}} 换成同款米白色"


def test_prompt_override_is_rejected_when_template_is_fixed(client: TestClient) -> None:
    """`editable` 为空 = 作者关掉了改写入口，不是"随便写"。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["prompt"] = {"locked": "固定画面描述 {{slot:hero}}", "editable": ""}
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "promptOverride": "我自己的描述"},
        headers=headers,
    )
    assert body.status_code == 400
    assert "不能修改" in body.json()["message"]


def test_prompt_override_cannot_hardcode_numbers(client: TestClient) -> None:
    """填表侧也拦 `{{Mixed N}}`：写死编号会在少填一张时整体错位。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "promptOverride": "把 {{Mixed 1}} 换掉"},
        headers=headers,
    )
    assert body.status_code == 400
    assert "不要直接写" in body.json()["message"]
