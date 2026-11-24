"""模板案例接口：发布 / 列表口径 / 状态流转 / 越权与权限。

这些用例全部走 HTTP —— 要验的正是「路由 + 权限 + 序列化」这一层。

用例盯的是**规则**，不是实现细节：

- 案例与模板的关联：发布的是**快照**，模板后来改了不影响已发布的案例；
- 列表口径：`library` 只看得到已发布的，`mine` 看得到自己下架的，`trash` 是回收站；
- 状态机：下架 → 不在案例库；上架 → 回到案例库；删除 → 进回收站，可恢复；
- 越权读别人的下架案例回 **404**（不是 403：403 等于确认「这个案例存在」）。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, set_visibility, suffix
from tests_template_helpers import png_bytes, upload, upload_shared, video_bytes

API = "/api/v1/template-cases"
SPECS = "/api/v1/template-specs"
MODEL = "libtv-wan3-replacement"

#: 能读案例的人：看案例库、读案例详情。
READ_PERMS = ["template_pack:read", "template_pack:case:read", *LIB_RW]
#: 能管理案例的人：发布 / 编辑 / 下架 / 删除。
WRITE_PERMS = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:case:read",
    "template_pack:case:write",
    "template_pack:publish",
    *LIB_RW,
]
#: 只读案例（没有 case:write）的人：能看能复用，不能改。
_READONLY_PERMS = ["template_pack:read", "template_pack:case:read", *LIB_RW]

_TAG = "tag-case-tests"


def _user(client: TestClient, perms: list[str]) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, perms)
    return headers


def _media(client: TestClient, headers: dict[str, str]) -> tuple[str, str, str]:
    # 团队可见：模板发布时固定槽位的素材必须所有复用者都读得到（发布闸）。
    # 案例是「发布出去给别人复用」的，素材对外不可见的话案例一发布就是坏的。
    video = upload_shared(
        client, f"case-{suffix()}.mp4", "source", video_bytes(2, audio=True), headers=headers
    )
    first = upload_shared(client, f"c-{suffix()}.png", "image", png_bytes(), headers=headers)
    second = upload_shared(client, f"d-{suffix()}.png", "image", png_bytes(), headers=headers)
    return str(video["id"]), str(first["id"]), str(second["id"])


def _draft(video: str, *, key: str | None = None, name: str = "演示模板") -> dict[str, Any]:
    return {
        "key": key or f"case-spec-{suffix()}",
        "name": name,
        "model": MODEL,
        "params": {},
        "prompt": "8 秒 · 9:16 竖屏\n\n{{slot:hero}} 与 {{slot:note*}}；原片 {{slot:source}}",
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
                "tagIds": [_TAG],
                "minCount": 1,
                "maxCount": 1,
                "position": 1,
                "label": "主图",
            },
            {
                "key": "note",
                "type": "image",
                "binding": "user",
                "tagIds": [_TAG],
                "minCount": 0,
                "maxCount": 1,
                "position": 2,
                "label": "细节",
            },
        ],
    }


def _template(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    """建一个模板（有草稿即可，不必发版）。"""
    video, _first, _second = _media(client, headers)
    draft = _draft(video)
    response = client.post(SPECS, json=draft, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _publish(
    client: TestClient,
    headers: dict[str, str],
    spec_key: str,
    *,
    name: str | None = None,
    category: str = "广告片",
) -> dict[str, Any]:
    response = client.post(
        API,
        json={
            "specKey": spec_key,
            "name": name or f"案例-{suffix()}",
            "description": "一个可复用的案例",
            "category": category,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------- #
# 发布
# --------------------------------------------------------------------------- #
def test_publish_case_snapshots_the_draft(client: TestClient) -> None:
    """案例存的是**发布那一刻的模板配置快照**。

    快照里能看到槽位与提示词，且带着源模板的 key —— 复用时由前端换成新 key。
    """
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    case = _publish(client, headers, spec["key"])

    assert case["status"] == "published"
    assert case["specKey"] == spec["key"]
    assert case["specId"] == spec["id"]
    assert case["category"] == "广告片"
    assert case["sourceMissing"] is False
    # 快照带上了模板配置（槽位是 camelCase，与前端契约一致）
    assert case["config"]["key"] == spec["key"]
    assert {slot["key"] for slot in case["config"]["slots"]} == {"source", "hero", "note"}
    assert case["config"]["slots"][0]["assetId"] == spec["draft"]["slots"][0]["assetId"]


def test_publish_case_takes_the_latest_draft(client: TestClient) -> None:
    """模板改了草稿之后发布的案例，拿到的是**新**草稿，不是发版时的老快照。

    快照取草稿而不是「上一个发版版本」：作者点「发布为案例」时心里想的就是
    眼前这份配置。
    """
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    video = spec["draft"]["slots"][0]["assetId"]
    draft = _draft(video, key=spec["key"], name="演示模板")
    draft["prompt"] = "改过的提示词 {{slot:hero}}"
    saved = client.post(SPECS, json=draft, headers=headers)
    assert saved.status_code == 200, saved.text

    case = _publish(client, headers, spec["key"])
    assert case["config"]["prompt"] == "改过的提示词 {{slot:hero}}"


def test_publish_case_of_other_users_template_is_rejected(client: TestClient) -> None:
    """案例只能从**自己的**模板发布。"""
    owner = _user(client, WRITE_PERMS)
    other = _user(client, WRITE_PERMS)
    spec = _template(client, owner)

    response = client.post(
        API,
        json={"specKey": spec["key"], "name": f"越权-{suffix()}", "category": ""},
        headers=other,
    )
    assert response.status_code in (403, 409), response.text


def test_publish_case_requires_publish_permission(client: TestClient) -> None:
    """只有案例写码、没有模板发布码 → 不能发布。

    发布案例等于把配置公开出去，与发布模板是同一类对外动作；只给案例写码
    就等于留了一条绕过模板发布闸口的路。
    """
    headers = _user(
        client,
        ["template_pack:read", "template_pack:create", "template_pack:case:write", *LIB_RW],
    )
    spec = _template(client, headers)
    response = client.post(
        API,
        json={"specKey": spec["key"], "name": f"缺发布码-{suffix()}", "category": ""},
        headers=headers,
    )
    assert response.status_code == 403, response.text


def test_duplicate_case_name_is_rejected(client: TestClient) -> None:
    """同一个发布者名下不能有同名案例（否则列表里分不清谁是谁）。"""
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    name = f"同名-{suffix()}"
    _publish(client, headers, spec["key"], name=name)

    response = client.post(
        API,
        json={"specKey": spec["key"], "name": name, "category": ""},
        headers=headers,
    )
    assert response.status_code == 409, response.text


# --------------------------------------------------------------------------- #
# 列表口径
# --------------------------------------------------------------------------- #
def test_library_lists_published_cases_of_others(client: TestClient) -> None:
    """案例库对所有人开放：别人的已发布案例也要看得到（复用的前提）。"""
    owner = _user(client, WRITE_PERMS)
    reader = _user(client, _READONLY_PERMS)
    spec = _template(client, owner)
    case = _publish(client, owner, spec["key"], name=f"共享-{suffix()}")

    response = client.get(API, headers=reader)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    ids = [item["id"] for item in data["items"]]
    assert case["id"] in ids
    assert data["total"] == len(data["items"])


def test_scope_mine_includes_offline_and_trash_holds_deleted(client: TestClient) -> None:
    """三档口径互不串味：案例库只放已发布的，「我的」含下架，回收站放删掉的。"""
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    published = _publish(client, headers, spec["key"], name=f"在架-{suffix()}")
    offline = _publish(client, headers, spec["key"], name=f"下架-{suffix()}")
    gone = _publish(client, headers, spec["key"], name=f"删了-{suffix()}")

    assert client.post(f"{API}/{offline['id']}/offline", headers=headers).status_code == 200
    assert client.delete(f"{API}/{gone['id']}", headers=headers).status_code == 200

    library = client.get(API, headers=headers).json()["data"]
    library_ids = {item["id"] for item in library["items"]}
    assert published["id"] in library_ids
    assert offline["id"] not in library_ids
    assert gone["id"] not in library_ids

    mine = client.get(f"{API}?scope=mine", headers=headers).json()["data"]
    mine_ids = {item["id"] for item in mine["items"]}
    assert offline["id"] in mine_ids
    assert gone["id"] not in mine_ids

    trash = client.get(f"{API}?scope=trash", headers=headers).json()["data"]
    trash_ids = {item["id"] for item in trash["items"]}
    assert gone["id"] in trash_ids
    assert published["id"] not in trash_ids


def test_categories_are_deduped(client: TestClient) -> None:
    """分类清单给筛选器用：去重，且不把别人的未分类也算进来。"""
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    _publish(client, headers, spec["key"], name=f"分类A-{suffix()}", category="广告片")
    _publish(client, headers, spec["key"], name=f"分类B-{suffix()}", category="广告片")
    _publish(client, headers, spec["key"], name=f"分类C-{suffix()}", category="短剧")

    data = client.get(API, headers=headers).json()["data"]
    assert data["categories"].count("广告片") == 1
    assert "短剧" in data["categories"]


def test_keyword_filter_matches_name(client: TestClient) -> None:
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    marker = f"独特词-{suffix()}"
    _publish(client, headers, spec["key"], name=marker)

    data = client.get(f"{API}?keyword={marker}", headers=headers).json()["data"]
    assert [item["name"] for item in data["items"]] == [marker]


# --------------------------------------------------------------------------- #
# 状态流转
# --------------------------------------------------------------------------- #
def test_offline_hides_from_library_and_online_brings_back(client: TestClient) -> None:
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    case = _publish(client, headers, spec["key"])

    offlined = client.post(f"{API}/{case['id']}/offline", headers=headers)
    assert offlined.status_code == 200, offlined.text
    assert offlined.json()["data"]["status"] == "offline"

    library = client.get(API, headers=headers).json()["data"]
    assert case["id"] not in {item["id"] for item in library["items"]}

    onlined = client.post(f"{API}/{case['id']}/online", headers=headers)
    assert onlined.status_code == 200, onlined.text
    library = client.get(API, headers=headers).json()["data"]
    assert case["id"] in {item["id"] for item in library["items"]}


def test_delete_is_soft_and_restorable(client: TestClient) -> None:
    """删除是软删：进回收站，能恢复 —— 与模板同一套语义。"""
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    case = _publish(client, headers, spec["key"])

    deleted = client.delete(f"{API}/{case['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    restored = client.post(f"{API}/{case['id']}/restore", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["status"] == "published"

    library = client.get(API, headers=headers).json()["data"]
    assert case["id"] in {item["id"] for item in library["items"]}


def test_edit_case_updates_name_and_category(client: TestClient) -> None:
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    case = _publish(client, headers, spec["key"])

    response = client.patch(
        f"{API}/{case['id']}",
        json={"name": f"改名-{suffix()}", "description": "新的描述", "category": "短剧"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["category"] == "短剧"
    assert data["description"] == "新的描述"


def test_edit_does_not_touch_the_source_template(client: TestClient) -> None:
    """编辑案例只改案例自己的字段 —— 模板本体一点不动。"""
    headers = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    case = _publish(client, headers, spec["key"])

    client.patch(
        f"{API}/{case['id']}",
        json={"name": f"改名-{suffix()}", "category": "短剧"},
        headers=headers,
    )

    again = client.get(f"{SPECS}/{spec['key']}", headers=headers).json()["data"]
    assert again["name"] == spec["name"]


# --------------------------------------------------------------------------- #
# 越权与权限
# --------------------------------------------------------------------------- #
def test_others_cannot_edit_or_offline_my_case(client: TestClient) -> None:
    headers = _user(client, WRITE_PERMS)
    other = _user(client, WRITE_PERMS)
    spec = _template(client, headers)
    case = _publish(client, headers, spec["key"])

    patched = client.patch(f"{API}/{case['id']}", json={"name": "改名"}, headers=other)
    assert patched.status_code in (403, 409)
    offlined = client.post(f"{API}/{case['id']}/offline", headers=other)
    assert offlined.status_code in (403, 409)
    deleted = client.delete(f"{API}/{case['id']}", headers=other)
    assert deleted.status_code in (403, 409)


def test_offline_case_of_others_reads_as_not_found(client: TestClient) -> None:
    """别人的下架案例回 **404**，不是 403。

    403 会确认「这个案例存在」，等于给 id 空间开了枚举口。
    """
    owner = _user(client, WRITE_PERMS)
    reader = _user(client, _READONLY_PERMS)
    spec = _template(client, owner)
    case = _publish(client, owner, spec["key"])
    client.post(f"{API}/{case['id']}/offline", headers=owner)

    response = client.get(f"{API}/{case['id']}", headers=reader)
    assert response.status_code == 404, response.text


def test_read_only_user_cannot_manage_cases(client: TestClient) -> None:
    """只有读码的人：能看，不能改。"""
    owner = _user(client, WRITE_PERMS)
    reader = _user(client, _READONLY_PERMS)
    spec = _template(client, owner)
    case = _publish(client, owner, spec["key"])

    assert client.get(API, headers=reader).status_code == 200
    assert client.get(f"{API}/{case['id']}", headers=reader).status_code == 200
    assert client.post(f"{API}/{case['id']}/offline", headers=reader).status_code == 403
    assert client.delete(f"{API}/{case['id']}", headers=reader).status_code == 403


def test_case_list_requires_case_read_permission(client: TestClient) -> None:
    """没有案例读码 → 403（而不是空列表）。"""
    headers = _user(client, ["template_pack:read", *LIB_RW])
    assert client.get(API, headers=headers).status_code == 403


def test_snapshot_hides_assets_the_reader_cannot_read(client: TestClient) -> None:
    """案例快照里**读不到的素材引用**要摘掉，槽位留着。

    复用的前提是「拿到一份能接着改的起点」：作者可能在固定槽位上用了一条
    自己可见、别人看不见的素材，引用原样带过去，复用者一存就撞
    「固定素材不可用或无权访问」—— 半路卡死，复用就没意义了。
    """
    owner = _user(client, WRITE_PERMS)
    reader = _user(client, _READONLY_PERMS)
    spec = _template(client, owner)
    case = _publish(client, owner, spec["key"])
    asset_id = spec["draft"]["slots"][0]["assetId"]
    # 素材改成「仅自己可见」：作者照样能引用，别人读不到
    set_visibility(asset_id, "personal")

    # 作者自己看自己的案例：素材引用原样保留
    mine = client.get(f"{API}/{case['id']}", headers=owner).json()["data"]
    assert mine["config"]["slots"][0]["assetId"] == asset_id

    # 别人读不到那条素材 → 引用被摘掉，槽位本身还在
    theirs = client.get(f"{API}/{case['id']}", headers=reader).json()["data"]
    slot = theirs["config"]["slots"][0]
    assert slot["key"] == "source"
    assert slot["assetId"] is None
