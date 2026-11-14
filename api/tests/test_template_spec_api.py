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
PERMS = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:publish",
    "catalog:read",
    *LIB_RW,
]
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
        "prompt": "8 秒 · 9:16 竖屏\n\n"
        + (editable or "{{slot:hero}} 与 {{slot:note*}}；原片 {{slot:source}}"),
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
    changed = dict(draft, prompt="8 秒 · 9:16 竖屏\n\n{{slot:hero}} 换掉")
    _save(client, headers, changed)
    second_publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert second_publish.json()["data"]["version"] == 2

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["currentVersion"] == 2
    assert detail["latest"]["version"] == 2
    assert detail["latest"]["prompt"] == "8 秒 · 9:16 竖屏\n\n{{slot:hero}} 换掉"
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
    """`type=pack` 的槽位由资产库给成员与顺序，编译只照抄。"""
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
    draft["prompt"] = "{{slot:detail*}} 换成 {{slot:source}}"
    draft["slots"] = [
        draft["slots"][0],
        {
            "key": "detail",
            # 槽位类型就是资产库的 kind 之一：`pack` = 要一整个封装。
            "type": "pack",
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


def test_delete_moves_spec_to_trash_and_restore_brings_it_back(
    client: TestClient,
) -> None:
    """删除是软删：列表与模板库都看不见，回收站能捞回来，key 还能继续用。

    硬删会让 `template_spec_versions` 里那份「当时的提示词原文」跟着消失，
    历史任务的溯源就断了 —— 所以这条守的是「删了还在」。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    assert client.delete(f"{API}/{draft['key']}", headers=headers).status_code == 200

    alive = client.get(API, params={"pageSize": 100}, headers=headers).json()["data"]
    assert draft["key"] not in {item["key"] for item in alive["items"]}
    published = client.get(f"{API}/published", params={"pageSize": 100}, headers=headers)
    assert draft["key"] not in {item["key"] for item in published.json()["data"]["items"]}
    # 已删的 key 不能再当活模板用
    assert client.get(f"{API}/{draft['key']}", headers=headers).status_code == 404

    trash = client.get(f"{API}/trash", headers=headers).json()["data"]
    assert draft["key"] in {item["key"] for item in trash["items"]}

    back = client.post(f"{API}/{draft['key']}/restore", headers=headers)
    assert back.status_code == 200, back.text
    assert back.json()["data"]["deletedAt"] is None
    assert client.get(f"{API}/{draft['key']}", headers=headers).status_code == 200
    # 恢复后发布版本还在，模板库照常能看到
    published = client.get(f"{API}/published", params={"pageSize": 100}, headers=headers)
    assert draft["key"] in {item["key"] for item in published.json()["data"]["items"]}

    # 清理：别给别的用例留下这份模板
    client.delete(f"{API}/{draft['key']}", headers=headers)


def test_restore_rejects_a_spec_that_is_not_in_trash(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    response = client.post(f"{API}/{draft['key']}/restore", headers=headers)
    assert response.status_code == 409
    assert "不在回收站" in response.json()["message"]


def test_text_literal_is_rejected_like_mixed(client: TestClient) -> None:
    """`{{Text N}}` 与 `{{Mixed N}}` 一样是派生编号，手写就错位。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, editable="把 {{Text 1}} 念出来")
    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400
    assert "不要直接写" in response.json()["message"]


def test_demo_asset_round_trips_but_never_enters_the_snapshot(
    client: TestClient,
) -> None:
    """展示案例只存在草稿里：改案例不该要求发新版本，也不该进发布快照。

    模板库卡片与详情读的是 `demoAssetId`（出参顶层字段），发布快照
    （`latest`）里没有它 —— 这条守的是"它会丢"和"它不该在那里"两种错。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["demoAssetId"] = first
    saved = _save(client, headers, draft)
    assert saved["demoAssetId"] == first

    client.post(f"{API}/{draft['key']}/publish", headers=headers)
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["demoAssetId"] == first
    assert "demoAssetId" not in (detail["latest"] or {})

    # 换一张案例：只改草稿，版本号不动
    again = dict(draft, demoAssetId=second)
    updated = _save(client, headers, again)
    assert updated["demoAssetId"] == second
    assert updated["currentVersion"] == 1


def test_slot_source_field_is_gone_and_rejected(client: TestClient) -> None:
    """`source` 并进 `type` 之后，旧字段必须**当场被拒**（422）。

    静默忽略更糟：封装页把 `type` 当成「要什么」的唯一表达之后，还收着
    `source=pack` 的客户端会以为自己在提交一个封装槽位，实际提交的是一个
    `type=image` 的普通槽位 —— 直到编译才发现少了成员。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"][1]["source"] = "pack"

    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 422, response.text
    assert "source" in response.text


def test_slot_tag_limits_the_candidate_set_but_not_the_compile(
    client: TestClient,
) -> None:
    """一级标签只是**候选集范围**：存得住、回得来，但编译不看它。

    封装页用它把「封装时固定」与「模板库填写」两处选择器收窄到同一个一级标签下；
    而提示词编号、参考素材顺序与它无关 —— 标签被删、被改名，已发布的版本照旧能编译。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    tag = client.post(
        "/api/v1/library/tags", json={"name": f"产品-{suffix()}"}, headers=headers
    )
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["data"]["id"]

    draft = _draft(video, first, second)
    draft["slots"][1]["tagId"] = tag_id
    saved = _save(client, headers, draft)
    assert saved["draft"]["slots"][1]["tagId"] == tag_id

    client.post(f"{API}/{draft['key']}/publish", headers=headers)
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["slots"][1]["tagId"] == tag_id

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    # 编号与参考序完全不受标签影响
    assert body.json()["data"]["prompt"] == "8 秒 · 9:16 竖屏\n\n{{Mixed 2}} 与 ；原片 {{Mixed 1}}"


def test_compile_no_longer_accepts_prompt_override(client: TestClient) -> None:
    """提示词不再分固定段 / 可编辑段，`promptOverride` 这个入口一并取消。

    要可变内容就放一个 `text` 槽位：填表时选哪份文案，`compile` 就把它排在
    `{{Text N}}` 的第 N 位。字段本身被 `extra="forbid"` 拒掉，不是静默忽略 ——
    旧前端若还传它，会当场 422 而不是"看着成功、内容没改"。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}, "promptOverride": "我自己的描述"},
        headers=headers,
    )
    assert body.status_code == 422, body.text


def test_text_slot_is_the_only_way_to_make_prompt_variable(client: TestClient) -> None:
    """文本槽位 → `{{Text N}}` + `textLibraryIds`：模板库换文案不改模板。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    note = upload(client, f"文案-{suffix()}.md", "text", "# 标题\n正文".encode(), headers=headers)
    draft = _draft(video, first, second)
    draft["prompt"] = "{{slot:hero}}；口播文案：{{slot:brief}}"
    draft["slots"] = [
        draft["slots"][0],
        draft["slots"][1],
        {
            "key": "brief",
            "type": "text",
            "binding": "user",
            "refMode": "attachment",
            "position": 2,
            "label": "口播文案",
        },
    ]
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first], "brief": [note["id"]]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["prompt"] == "{{Mixed 2}}；口播文案：{{Text 1}}"
    assert data["textLibraryIds"] == [note["id"]]


def test_execution_prompt_limit_matches_task_schema() -> None:
    """提示词上限是**执行端点**的属性：常量必须和 `CreateTaskIn.prompt` 的 `max_length` 一致。

    两处各写一个数字，漂移的表现是「模板能发布、填表能编译、点生成才 422」——
    用户已经把素材填完了，错却出在别处。所以钉成一条断言，改哪边都会先红。
    """
    from annotated_types import MaxLen

    from core.template_spec import EXECUTION_PROMPT_LIMIT
    from modules.task.schema import CreateTaskIn

    limits = [
        item.max_length
        for item in CreateTaskIn.model_fields["prompt"].metadata
        if isinstance(item, MaxLen)
    ]
    assert limits == [EXECUTION_PROMPT_LIMIT]


def test_draft_can_be_long_but_publish_checks_execution_limit(client: TestClient) -> None:
    """草稿允许 20000 字（编写上限），发布要求能跑（执行上限 2000 字）。"""
    from core.template_spec import EXECUTION_PROMPT_LIMIT

    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["prompt"] = "固定画面" + "长" * (EXECUTION_PROMPT_LIMIT + 50)
    # 保存草稿不挡：作者可能只是先存着，或者这段是给别的执行端点准备的。
    assert client.post(API, json=draft, headers=headers).status_code == 200

    publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert publish.status_code == 400
    assert "执行层上限" in publish.json()["message"]



def test_models_expose_replacement_declaration(client: TestClient) -> None:
    """能否跑替换链由**模型**声明，目录必须出参。

    调用方不许从槽位组合推断（"有 fixed video 槽 + 有 pack 槽就是替换模板"）：
    普通多图模板会被误判，然后在替换链路里以「没有绑定档案」失败。
    """
    body = client.get("/api/v1/models", headers=_headers(client))
    assert body.status_code == 200, body.text
    items = {item["id"]: item for item in body.json()["data"]["items"]}
    for model in ("libtv-wan3-replacement", "bailian-wan3-replacement"):
        assert items[model]["replacement"] is True, model
    assert items["agnes-video-2.5-flash"]["replacement"] is False


def test_spec_without_slots_compiles_to_a_plain_prompt(client: TestClient) -> None:
    """槽位数量**下不封底**：纯提示词模板一个槽位都没有，也能发布、能编译。

    守卫的是"至少一个槽位"这个常见的过度约束 —— 它在服务端并不存在
    （`slots` 默认空列表），却在封装页被硬编码成"只有一个槽位时不给删"。
    那种限制会让用户为了发一份纯提示词模板，被迫留下一个永远填不满的槽位。
    """
    headers = _headers(client)
    draft = {
        "key": f"plain-{suffix()}",
        "name": "纯提示词",
        "model": "agnes-video-2.5-flash",
        "params": {"aspectRatio": "9:16", "durationSec": 5},
        "prompt": "一只猫在窗台上打哈欠",
        "slots": [],
    }
    _save(client, headers, draft)
    assert client.post(f"{API}/{draft['key']}/publish", headers=headers).status_code == 200

    body = client.post(
        f"{API}/{draft['key']}/compile", json={"slotValues": {}}, headers=headers
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["prompt"] == "一只猫在窗台上打哈欠"
    assert data["referenceLibraryIds"] == []
    assert data["textLibraryIds"] == []
    assert data["slots"] == []


def test_published_filters_by_model_replacement(client: TestClient) -> None:
    """`?target=` 按模型声明筛，不按槽位形状猜。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)

    replace_spec = _draft(video, first, second)
    _save(client, headers, replace_spec)
    assert client.post(f"{API}/{replace_spec['key']}/publish", headers=headers).status_code == 200

    plain = {
        "key": f"plain-{suffix()}",
        "name": "纯提示词",
        "model": "agnes-video-2.5-flash",
        "params": {"aspectRatio": "9:16", "durationSec": 5},
        "prompt": "一段描述",
        "slots": [],
    }
    _save(client, headers, plain)
    assert client.post(f"{API}/{plain['key']}/publish", headers=headers).status_code == 200

    def keys(target: str) -> set[str]:
        query = client.get(
            f"{API}/published",
            params={"target": target, "pageSize": 100},
            headers=headers,
        )
        assert query.status_code == 200, query.text
        return {item["key"] for item in query.json()["data"]["items"]}

    assert replace_spec["key"] in keys("replacement")
    assert plain["key"] not in keys("replacement")
    assert plain["key"] in keys("generation")
    assert replace_spec["key"] not in keys("generation")
