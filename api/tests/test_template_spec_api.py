"""模板规格接口：预检 / 保存幂等 / 发布冻结 / 编译派生编号。

这些用例全部走 HTTP，不直接调 service —— 要验的正是"路由 + 权限 + 序列化"这一层，
而纯规则已经由 `test_template_spec_compile.py` 覆盖。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, set_visibility, suffix
from tests_template_helpers import png_bytes, upload, upload_shared, video_bytes

from core.template_spec import EXECUTION_PROMPT_LIMIT, MAX_PROMPT

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
    # 团队可见：模板发布时固定槽位的素材必须所有复用者都读得到（发布闸）。
    # 给私有素材的话，用例红在「发布被拒」上，测不到本来要测的那件事。
    video = upload_shared(
        client, f"clip-{suffix()}.mp4", "source", video_bytes(2, audio=True), headers=headers
    )
    first = upload_shared(client, f"a-{suffix()}.png", "image", png_bytes(), headers=headers)
    second = upload_shared(client, f"b-{suffix()}.png", "image", png_bytes(), headers=headers)
    return str(video["id"]), str(first["id"]), str(second["id"])


#: 用户填的图片槽位必须圈标签（发布前校验），这些用例不关心标签本身，
#: 用同一个占位 id —— 发布只检查"有没有给"，不检查"标签还在不在"（那个失败
#: 在填表页表现为"这个标签下没有素材"，不拦发布）。
_TAG = "tag-spec-tests"


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
    draft.update(over)
    return draft


def _save(client: TestClient, headers: dict[str, str], draft: dict[str, Any]) -> dict[str, Any]:
    response = client.post(API, json=draft, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_limits_are_served_from_the_backend(client: TestClient) -> None:
    """编辑页显示的上限**从后端取**，前端不再各抄一份常量。

    两个数说的是不同的事：`promptMax` 是存草稿的上限（草稿允许很长），
    `promptExecutionLimit` 是**能不能发布**的上限（按原文字数比）。

    顺带钉住路由顺序：这条能通说明 `/limits` 没被 `/{key}` 当成一个规格 key
    （被吞掉的话这里是 404「配置不存在：limits」）。
    """
    headers = _headers(client)
    body = client.get(f"{API}/limits", headers=headers)

    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["promptMax"] == MAX_PROMPT
    assert data["promptExecutionLimit"] == EXECUTION_PROMPT_LIMIT


def test_validate_returns_fingerprint(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    body = client.post(f"{API}/validate", json=_draft(video, first, second), headers=headers)
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert data["valid"] is True
    assert len(data["fingerprint"]) == 64


def test_mixed_refs_are_the_author_facing_syntax(client: TestClient) -> None:
    """`{{Mixed N}}` 现在是**作者写的**写法：N = 第 N 个图片/成片槽位。

    以前它被当成「写死编号」拒掉（编号看起来该由编译器派生）。统一之后
    作者写的与模型看到的是同一种东西，编译器负责展开成**本次实际**的编号。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)

    draft = _draft(video, first, second, editable="{{Mixed 2}} 替换成 {{Mixed 1}}")
    body = client.post(f"{API}/validate", json=draft, headers=headers)
    assert body.status_code == 200, body.text

    # 存下来的就是作者写的形态，不偷偷换成内部 key
    saved = _save(client, headers, draft)
    assert saved["draft"]["prompt"].endswith("{{Mixed 2}} 替换成 {{Mixed 1}}")


def test_mixed_ref_out_of_range_is_rejected(client: TestClient) -> None:
    """写 `{{Mixed 9}}` 而只有 3 个视觉槽位：当场拒，且说清有几个。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second, editable="{{Mixed 9}}")
    body = client.post(f"{API}/validate", json=draft, headers=headers)
    assert body.status_code == 400
    assert "找不到对应槽位" in body.json()["message"]


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


def test_publish_after_disable_brings_the_spec_back(client: TestClient) -> None:
    """停用后重新发布 = 重新上线。

    回归：草稿一个字都没改就重新发布，会命中原有的**幂等分支**
    （同一份草稿重发 → 版本号不动、不新增快照行）。那个分支以前只 return、
    不动 status，于是接口给 200 而记录仍是 disabled —— 界面拿到成功、
    模板却还挂在下架状态。

    这条用例的判别力全在「草稿没改」上：先发布一次，停用，再原样发布。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)

    first_publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert first_publish.status_code == 200, first_publish.text
    version = first_publish.json()["data"]["version"]
    published_at = first_publish.json()["data"]["spec"]["publishedAt"]

    off = client.post(f"{API}/{draft['key']}/disable", headers=headers)
    assert off.json()["data"]["status"] == "disabled"
    assert off.json()["data"]["disabledAt"] is not None

    back = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert back.status_code == 200, back.text
    spec = back.json()["data"]["spec"]
    assert spec["status"] == "published"
    assert spec["disabledAt"] is None
    # 没有新版本：幂等分支不该凭空造一个版本号
    assert back.json()["data"]["version"] == version
    # 也没有新发布：publishedAt 说的是「这个版本首次发布的时间」。
    assert spec["publishedAt"] == published_at

    # 真的能用了 —— 状态恢复不是只写在出参里
    body = client.post(f"{API}/{draft['key']}/compile", json={"slotValues": {}}, headers=headers)
    assert body.status_code == 400  # 缺必填素材，但不再是 409「已停用」


def test_disabled_spec_can_be_republished_with_a_changed_draft(client: TestClient) -> None:
    """改过草稿再发布走的是另一条分支（新增版本），状态同样要恢复。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)
    first_publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    version = first_publish.json()["data"]["version"]

    client.post(f"{API}/{draft['key']}/disable", headers=headers)

    draft["prompt"] = f"{draft['prompt']}\n停用后改过的句子。"
    _save(client, headers, draft)
    back = client.post(f"{API}/{draft['key']}/publish", headers=headers)

    assert back.status_code == 200, back.text
    assert back.json()["data"]["version"] == version + 1
    assert back.json()["data"]["spec"]["status"] == "published"
    assert back.json()["data"]["spec"]["disabledAt"] is None


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


def test_user_filled_media_slot_must_declare_a_tag(
    client: TestClient,
) -> None:
    """要让用户填的图片/视频槽位，发布前必须圈至少一个标签。

    模板库的填表区按「标签 · 槽位名」分组，标签当分档；没有标签时那一段
    只能退化成"全部素材"，素材一多就没法挑。所以这是**发布闸门**，而不是填表时
    才提醒 —— 发布时作者还在，能立刻补；等用户填到一半才发现，他补不了。

    固定槽位不要求：内容作者已经选死，模板使用者根本不挑。草稿也不拦（存得下才谈得上补）。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    for slot in draft["slots"]:
        slot.pop("tagIds", None)

    # 草稿能存
    _save(client, headers, draft)

    # 预检与发布都拦，并指名是哪几个槽位
    checked = client.post(f"{API}/validate", json=draft, headers=headers)
    assert checked.status_code == 400, checked.text
    assert "标签" in checked.json()["message"]
    assert "主图" in checked.json()["message"]

    blocked = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert blocked.status_code == 400, blocked.text
    assert "标签" in blocked.json()["message"]

    # 只给一个不够：两个都要
    draft["slots"][1]["tagIds"] = [_TAG]
    still = client.post(f"{API}/validate", json=draft, headers=headers)
    assert still.status_code == 400, still.text
    assert "细节" in still.json()["message"]

    draft["slots"][2]["tagIds"] = [_TAG]
    ok = client.post(f"{API}/validate", json=draft, headers=headers)
    assert ok.status_code == 200, ok.text


def test_fixed_slot_does_not_need_a_tag(client: TestClient) -> None:
    """固定槽位不参与"按标签分组挑素材"，所以不要求标签。

    `原片` 是 `binding=fixed` 的 video 槽位：作者选死了，模板使用者只读。
    要求它也给标签只是多一步，改不出任何效果。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    for slot in draft["slots"]:
        slot.pop("tagIds", None)
    draft["slots"] = [draft["slots"][0]]  # 只剩那个固定的 video 槽位
    draft["prompt"] = "{{slot:source}} 里的画面"

    body = client.post(f"{API}/validate", json=draft, headers=headers)
    assert body.status_code == 200, body.text


def test_slot_source_field_is_gone_and_rejected(client: TestClient) -> None:
    """旧的 `source` 轴必须**当场被拒**（422）。

    静默忽略更糟：客户端以为字段被收下了，实际提交的是另一个意思的槽位，
    直到编译才发现少了东西。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"][1]["source"] = "asset"

    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 422, response.text
    assert "source" in response.text


def test_slot_tag_limits_the_candidate_set_but_not_the_compile(
    client: TestClient,
) -> None:
    """标签只是**候选集范围**：存得住、回得来，但编译不看它。

    封装页用它把「封装时固定」与「模板库填写」两处选择器收窄到同一批标签下；
    而提示词编号、参考素材顺序与它无关 —— 标签被删、被改名，已发布的版本照旧能编译。

    槽位现在圈的是**一组**标签（任意级别）：候选集是这几个标签各自子树的并集。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    tag = client.post(
        "/api/v1/library/tags", json={"name": f"产品-{suffix()}"}, headers=headers
    )
    assert tag.status_code == 201, tag.text
    tag_id = tag.json()["data"]["id"]
    other = client.post(
        "/api/v1/library/tags", json={"name": f"角色-{suffix()}"}, headers=headers
    )
    assert other.status_code == 201, other.text
    other_id = other.json()["data"]["id"]

    draft = _draft(video, first, second)
    draft["slots"][1]["tagIds"] = [tag_id, other_id]
    saved = _save(client, headers, draft)
    assert saved["draft"]["slots"][1]["tagIds"] == [tag_id, other_id]

    client.post(f"{API}/{draft['key']}/publish", headers=headers)
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["slots"][1]["tagIds"] == [tag_id, other_id]

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    # 编号与参考序完全不受标签影响
    assert body.json()["data"]["prompt"] == "8 秒 · 9:16 竖屏\n\n{{Mixed 2}} 与 ；原片 {{Mixed 1}}"


def test_legacy_single_tag_slot_is_still_readable(client: TestClient) -> None:
    """老槽位只有 `tagId`（一个）—— 读回来时当成 `tagIds` 的第一项。

    为什么要有这条：契约从 `tagId` 换成了 `tagIds`，而 `extra="forbid"` 会把
    老草稿里的 `tagId` 判成"未知键"，那份草稿**一打开就 422** —— 模板还在，
    只是没人能再编辑它。老模板要能继续用、也能继续改。

    这里走的是**契约层**的回退（草稿 JSON 里的旧键）；已发布槽位行那一侧的回退
    在 `spec_service._slot_tag_ids`（新列 `tag_ids` 为空就退回旧的 `tag_id`），
    由 `test_template_spec_models.py` 与迁移用例守着。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    for slot in draft["slots"]:
        slot.pop("tagIds", None)
    draft["slots"][1]["tagId"] = _TAG
    draft["slots"][2]["tagId"] = _TAG

    saved = _save(client, headers, draft)
    assert saved["draft"]["slots"][1]["tagIds"] == [_TAG]

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["slots"][1]["tagIds"] == [_TAG]
    assert detail["latest"]["slots"][2]["tagIds"] == [_TAG]


def test_legacy_slot_usage_is_dropped_instead_of_rejected(client: TestClient) -> None:
    """老槽位里还带着 `usage`（「进提示词的用途说明」）—— 读回来时丢掉，不要 422。

    为什么要有这条：`usage` **从来没有进过提示词**（编译器完全不看它），
    所以它被删掉了（迁移 `d7f2a91c48b3` 删列）。但 `extra="forbid"` 会把
    老草稿里的 `usage` 判成「未知键」—— 那份草稿**一打开就 422**，
    模板还在，只是没人能再编辑它。老模板要能继续用、也能继续改。

    与老 `tagId` → `tagIds`、`packId` 丢弃是同一套处理：旧键在契约层
    被翻译掉，而不是逼人去改库。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    for slot in draft["slots"]:
        slot["usage"] = "确定同款同色外观"

    saved = _save(client, headers, draft)
    # 存得进去（没被 422 打回），且读回来时这个键已经不在了
    assert "usage" not in saved["draft"]["slots"][0]

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert "usage" not in detail["latest"]["slots"][0]


def test_slot_contract_no_longer_exposes_usage(client: TestClient) -> None:
    """`usage` 从契约里彻底消失：出参里也没有它。

    判据不能只盯入参（`extra="forbid"` 会拒），出参里还留着它的话，
    前端就会继续读那个字段、继续把它显示出来 —— 删了等于没删。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    _save(client, headers, draft)

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert "usage" not in detail["draft"]["slots"][0]
    assert "usage" not in detail["latest"]["slots"][0]


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
    """草稿允许 20000 字（编写上限），发布要求能跑（执行上限远小于它）。"""
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
    for model in (
        "libtv-wan3.0",
        "libtv-seedance-2.5",
        "libtv-seedance-2.5-flash",
        "libtv-seedance-2.0",
        "bailian-wan3-replacement",
    ):
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


def test_demo_asset_must_be_readable_by_the_author(client: TestClient) -> None:
    """`demoAssetId` 不能指向自己读不到的资产。

    它是**删除保护**的依据（`SpecDemoCases`）：写一个别人的私有 id 进去，
    那条资产从此永远 purge 不掉（409 说「仍被引用」），而作者根本不知道自己
    占了别人的东西；写个随手编的 id 更糟 —— 引用检查永远查无此人，卡在
    回收站里没有任何线索能定位。

    所以这里 fail-closed：读不到就拒绝保存。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)

    # 别人的私有素材：作者自己读不到
    from library_support import insert_asset

    someone_elses = insert_asset(
        owner_id="u_001",
        name="别人的私有封面.png",
        kind="image",
        state="active",
        visibility="personal",
    )
    denied = client.post(API, json=dict(draft, demoAssetId=someone_elses), headers=headers)
    assert denied.status_code == 400, denied.text
    assert "展示案例" in denied.json()["message"]

    # 不存在的 id：同样要挡住
    missing = client.post(API, json=dict(draft, demoAssetId="0" * 32), headers=headers)
    assert missing.status_code == 400, missing.text
    assert "展示案例" in missing.json()["message"]


def test_a_draft_may_reference_a_private_fixed_asset(client: TestClient) -> None:
    """草稿阶段**不**要求素材对外可见：那是作者自己的工作区。

    收紧的是**发布**那一刻。草稿里引用自己的私有素材完全正常，把它一起
    拦掉就等于逼作者在还没写完模板时就先处理素材的可见范围。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    set_visibility(video, "personal")
    draft = _draft(video, first, second)

    saved = client.post(API, json=draft, headers=headers)
    assert saved.status_code == 200, saved.text


def test_publish_rejects_a_fixed_asset_only_the_author_can_see(
    client: TestClient,
) -> None:
    """固定槽位的素材只有作者能看到 → **发布**被拒。

    复用者填表时是用**复用者的身份**再判一次可见性的（`_fills`），而固定
    槽位在编译期必须等于作者钉的那一条（core 的 `_assets_of`），复用者
    没法自己换一张。所以作者自己的私有素材一发布，模板对所有人就是坏的 ——
    错误还要延迟到别人点「生成」才炸出来。

    发布是唯一能拦住的那一刻。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    set_visibility(video, "personal")
    draft = _draft(video, first, second)
    _save(client, headers, draft)

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 400, published.text
    assert "只有你自己能看到" in published.json()["message"]


def test_publish_rejects_a_demo_only_the_author_can_see(client: TestClient) -> None:
    """展示案例只有作者能看到 → **发布**被拒。

    展示案例是模板库卡片上的那一帧：别人打开模板库看到的封面就是它。
    素材只有作者可见时，别人那里安静退化成「暂无案例」，卡片看着像没加载
    出来 —— 比明确报错更难查。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    set_visibility(first, "personal")
    draft = _draft(video, first, second, demoAssetId=first)
    _save(client, headers, draft)

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 400, published.text
    assert "只有你自己能看到" in published.json()["message"]


def test_other_users_cannot_read_a_draft_spec(client: TestClient) -> None:
    """草稿是作者私有的：提示词原文与固定素材 id 不是给别人看的。

    已发布是公开的（模板库要给所有人用），但**未发布**的只有作者能读 ——
    路由摘要写的就是「本人可见的规格列表」，实现漏了这层过滤。
    """
    author = _headers(client)
    stranger = _headers(client)
    video, first, second = _media(client, author)
    draft = _draft(video, first, second)
    _save(client, author, draft)

    # 别人的草稿详情：404 而不是 403（403 会确认「这个 key 存在」）
    detail = client.get(f"{API}/{draft['key']}", headers=stranger)
    assert detail.status_code == 404, detail.text

    # 别人的列表里也不该出现
    listed = client.get(API, headers=stranger).json()["data"]
    assert all(item["key"] != draft["key"] for item in listed["items"])

    # 发布之后就是公开的
    assert client.post(f"{API}/{draft['key']}/publish", headers=author).status_code == 200
    public = client.get(f"{API}/{draft['key']}", headers=stranger)
    assert public.status_code == 200, public.text
    published = client.get(f"{API}/published", headers=stranger).json()["data"]
    assert any(item["key"] == draft["key"] for item in published["items"])


# --------------------------------------------------------------------------- #
# 可选槽位：选中时文案 / 未选时文案
# --------------------------------------------------------------------------- #
def _optional_draft(
    video: str,
    first: str,
    *,
    key: str | None = None,
    prompt: str = "把 {{slot:source}} 里的内衣替换为 {{slot:hero}} 的同款产品",
    **slot_over: Any,
) -> dict[str, Any]:
    """一个**可选**素材槽位（`minCount=0`）的草稿。

    默认提示词用**单张引用** `{{slot:hero}}` —— 那种写法在没填素材时需要
    兜底文案，所以它是发布闸门要拦的那一种。
    """
    draft = _draft(video, first, first, key=key, editable=prompt)
    draft["slots"] = [
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
            "minCount": 0,
            "maxCount": 1,
            "position": 1,
            "label": "主图",
            **slot_over,
        },
    ]
    return draft


def test_optional_slot_publish_requires_the_fallback_text(client: TestClient) -> None:
    """可选槽位被**单张引用**却没有「未选时文案」→ 发布被拦。

    发布 = 别人会拿去用。不写兜底文案的话，使用者在模板库不填素材就会
    在点生成时被 400 打回，而那句话与「我刚才没选素材」看不出关系。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(video, first)
    _save(client, headers, draft)

    body = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert body.status_code == 400, body.text
    assert "未选时文案" in body.json()["message"]


def test_optional_slot_publish_is_allowed_with_the_fallback_text(
    client: TestClient,
) -> None:
    """写了「未选时文案」→ 发布通过。"""
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(video, first, emptyText="主角是穿红裙的年轻女性")
    _save(client, headers, draft)

    body = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert body.status_code == 200, body.text


def test_group_reference_does_not_require_the_fallback_text(client: TestClient) -> None:
    """整组引用（`{{slot:x*}}`）没填时降级为空 → **不要求**兜底文案。

    把整组引用也算进去，会把一堆本来好用的模板拦在发布外 ——
    而它们的使用者不填素材时根本不会报错（降级成空而已）。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video,
        first,
        prompt="把 {{slot:source}} 里的内衣替换为 {{slot:hero*}} 的同款产品",
    )
    _save(client, headers, draft)

    body = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert body.status_code == 200, body.text


def test_required_slot_with_slot_texts_is_rejected(client: TestClient) -> None:
    """**必填**槽位带「选中时文案 / 未选时文案」→ 400。

    **必填 = 素材一定在 = 直接插编号** —— 没有「没填」这个状态，两段文案
    对它没有意义。留着的话作者会以为那句文案管用，而界面上根本没这个框。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    # hero 是必填（minCount=1）
    draft["slots"][1]["filledText"] = "人物参考 {{slot:hero}}"

    body = client.post(API, json=draft, headers=headers)
    assert body.status_code == 400, body.text
    message = body.json()["message"]
    assert "必填" in message
    # 报错要说清要做什么：改成「可选」才能用这两段文案
    assert "可选" in message


def test_required_slot_with_empty_text_is_rejected(client: TestClient) -> None:
    """必填槽位带「未选时文案」同样被拒（它永远用不上）。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"][1]["emptyText"] = "主角是穿红裙的年轻女性"

    body = client.post(API, json=draft, headers=headers)
    assert body.status_code == 400, body.text
    assert "必填" in body.json()["message"]


def test_compile_uses_the_fallback_text_when_the_optional_slot_is_empty(
    client: TestClient,
) -> None:
    """使用者没填素材 → 提示词里用「未选时文案」，且不占编号。"""
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(video, first, emptyText="主角是穿红裙的年轻女性")
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile", json={"slotValues": {}}, headers=headers
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert "主角是穿红裙的年轻女性" in data["prompt"]
    # 没填 → 不进参考素材列表
    assert data["referenceLibraryIds"] == [video]


def test_compile_uses_the_filled_text_when_the_optional_slot_is_filled(
    client: TestClient,
) -> None:
    """使用者填了素材 → 用「选中时文案」，其中的自引用派生成编号。"""
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video,
        first,
        filledText="人物参考 {{slot:hero}}",
        emptyText="主角是穿红裙的年轻女性",
    )
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": {"hero": [first]}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    data = body.json()["data"]
    assert "人物参考 {{Mixed 2}}" in data["prompt"]
    assert data["referenceLibraryIds"] == [video, first]


def test_compile_uses_the_overridden_text_instead_of_the_authors(
    client: TestClient,
) -> None:
    """使用者改的「未选时文案」替换作者的，作者那句不再出现在提示词里。

    验的是**替换**而不是追加：两句话都在提示词里，等于用户改的那句是白加的
    旁白，而模型会同时收到两种场景描述。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(video, first, emptyText="主角是穿红裙的年轻女性")
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={
            "slotValues": {},
            "slotTextOverrides": {"hero": "主角是裹着灰色风衣的中年男人"},
        },
        headers=headers,
    )
    assert body.status_code == 200, body.text
    prompt = body.json()["data"]["prompt"]
    assert "主角是裹着灰色风衣的中年男人" in prompt
    assert "主角是穿红裙的年轻女性" not in prompt
    # 没填素材 → 不占编号、不进参考素材
    assert body.json()["data"]["referenceLibraryIds"] == [video]


def test_compile_override_works_for_a_group_reference(client: TestClient) -> None:
    """整组引用（`{{slot:hero*}}`）没填素材时也用兜底文案 —— 改的同样生效。

    整组引用的「没填」语义与单张不同（降级为空），但只要有兜底文案，
    `compile` 就用它接管那一块。所以这里不能只认单张引用。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video, first, prompt="原片 {{slot:source}}；产品参考 {{slot:hero*}}"
    )
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "不带产品，只保留人物"}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    assert "不带产品，只保留人物" in body.json()["data"]["prompt"]


def test_compile_override_recognises_the_mixed_token_form(
    client: TestClient,
) -> None:
    """作者写 `{{Mixed 2}}`（页面上的默认写法）也要认成插入点。

    现存模板清一色是这种写法，它在 `compile_spec` 内部才换算成 `{{slot:key}}`。
    改文案的判断如果只看 `{{slot:key}}`，**所有真实模板**都会被误判成
    「没有插入点」而 400 —— 这个坑正是真机验证踩出来的。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video,
        first,
        prompt="原片 {{Mixed 1}}，人物参考 {{Mixed 2}}",
        emptyText="主角是穿红裙的年轻女性",
    )
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "主角换成戴墨镜的男人"}},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    prompt = body.json()["data"]["prompt"]
    assert "主角换成戴墨镜的男人" in prompt
    assert "主角是穿红裙的年轻女性" not in prompt


def test_compile_override_without_an_insertion_point_is_rejected(
    client: TestClient,
) -> None:
    """提示词只写了第 N 张（`{{slot:hero.1}}`）→ 改文案不生效，所以 400。

    兜底文案只服务单张 / 整组引用；第 N 张是「第几张图」的编号，套不上一段话。
    静默收下的话，用户改的话没进提示词，而页面一切正常。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video, first, prompt="原片 {{slot:source}}，只参考第 1 张 {{slot:hero.1}}"
    )
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "换成海边的场景"}},
        headers=headers,
    )
    assert body.status_code == 400, body.text
    assert "插入点" in body.json()["message"]


def test_compile_override_checks_the_prompt_actually_being_compiled(
    client: TestClient,
) -> None:
    """插入点只在随机档那一段时：均衡档编译 → 400；随机档编译 → 生效。

    判据不能把两段提示词合并起来看 —— 合并会放行「均衡档改文案、可插入点
    只写在随机档里」这种情况，改的话静默丢掉。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video,
        first,
        prompt="原片 {{slot:source}}，只参考第 1 张 {{slot:hero.1}}",
        emptyText="红裙女性",
    )
    draft["randomPrompt"] = "原片 {{slot:source}}；人物 {{slot:hero}}"
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    balanced = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "换成海边的场景"}},
        headers=headers,
    )
    assert balanced.status_code == 400, balanced.text

    random = client.post(
        f"{API}/{draft['key']}/compile",
        json={"useRandomPrompt": True, "slotTextOverrides": {"hero": "换成海边的场景"}},
        headers=headers,
    )
    assert random.status_code == 200, random.text
    assert "换成海边的场景" in random.json()["data"]["prompt"]


def test_compile_override_on_a_required_slot_is_rejected(client: TestClient) -> None:
    """必填槽位没有「未选」这个状态，改兜底文案对它没有意义 → 400。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)  # hero 是必填（minCount=1）
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "随便写点什么"}},
        headers=headers,
    )
    assert body.status_code == 400, body.text
    assert "必填" in body.json()["message"]


def test_compile_override_without_assets_for_the_same_slot_is_rejected(
    client: TestClient,
) -> None:
    """同一格既挑了素材又改了文案 → 400（两者都在回答「这一格是什么」）。

    而且改的文案在有素材时根本不会被用（那时用「选中时文案」），静默取素材
    等于把用户写的字吞掉。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video, first, filledText="人物参考 {{slot:hero}}", emptyText="红裙女性"
    )
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={
            "slotValues": {"hero": [first]},
            "slotTextOverrides": {"hero": "不要素材，只要一句描述"},
        },
        headers=headers,
    )
    assert body.status_code == 400, body.text
    assert "二选一" in body.json()["message"]


def test_compile_override_rejects_empty_and_placeholder_text(
    client: TestClient,
) -> None:
    """空文案与含 `{{` 的文案都被拒，且报错点明是哪一格。

    空的话编译期会报「请在封装里写未选时文案」—— 与实际情形（用户刚清空了
    输入框）对不上；含占位符的话会在编译期炸成一句看不懂的残留引用错误。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(video, first, emptyText="红裙女性")
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    empty = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "   "}},
        headers=headers,
    )
    assert empty.status_code == 400, empty.text
    assert "不能为空" in empty.json()["message"]

    placeholder = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"hero": "参考 {{slot:source}}"}},
        headers=headers,
    )
    assert placeholder.status_code == 400, placeholder.text
    assert "槽位引用" in placeholder.json()["message"]

    unknown = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotTextOverrides": {"nope": "改写"}},
        headers=headers,
    )
    assert unknown.status_code == 400, unknown.text
    assert "不存在" in unknown.json()["message"]


def test_slot_texts_round_trip_through_the_contract(client: TestClient) -> None:
    """两个文案要能存进草稿、读回来（契约与持久化两侧都要通）。

    存的时候字段名是 camelCase（`filledText` / `emptyText`），读回来的
    快照里也是 —— 中间任何一侧漏了，作者写的东西就静默丢了。
    """
    headers = _headers(client)
    video, first, _ = _media(client, headers)
    draft = _optional_draft(
        video, first, filledText="人物参考 {{slot:hero}}", emptyText="红裙女性"
    )
    saved = _save(client, headers, draft)

    slot = next(item for item in saved["draft"]["slots"] if item["key"] == "hero")
    assert slot["filledText"] == "人物参考 {{slot:hero}}"
    assert slot["emptyText"] == "红裙女性"

    client.post(f"{API}/{draft['key']}/publish", headers=headers)
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    published = next(
        item for item in detail["latest"]["slots"] if item["key"] == "hero"
    )
    assert published["filledText"] == "人物参考 {{slot:hero}}"
    assert published["emptyText"] == "红裙女性"


def test_all_timestamps_come_back_with_the_same_shape(client: TestClient) -> None:
    """时间字段统一带 `Z`，不管这个值是从内存里来的还是从库重读的。

    **这条只在测试环境能复现** —— 用例跑的是 SQLite（不存时区）：同一条记录，
    刚 `utcnow()` 赋值时是 tz-aware、从库读回时是 naive，而出参若用裸
    `datetime`，pydantic 会按各自的样子序列化 —— 同一个字段在「创建」
    「再查详情」两个响应里长得不一样（`2026-11-19T01:00:00Z` vs
    `2026-11-19T01:00:00`）。

    生产用的是 PostgreSQL 的 `timestamp with time zone`，读回本来就是
    tz-aware，所以线上没出过这个症状。但「出参形状取决于当前挂的是哪种库」
    这件事本身就是隐患（换驱动、换库、换环境都会翻出来），
    `UtcDateTime` 把两条路的输出钉死成一种形状。

    前端 `new Date()` 两种写法都认，所以它不会崩；严格解析的客户端会偏 8 小时。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    saved = _save(client, headers, draft)

    # 刚保存、还没发布：这时候只有 createdAt / updatedAt 有值
    for field in ("createdAt", "updatedAt"):
        assert saved[field].endswith("Z"), f"{field}={saved[field]}"
    assert saved["publishedAt"] is None

    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    listed = client.get(API, headers=headers).json()["data"]["items"]
    row = next(item for item in listed if item["key"] == draft["key"])
    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]

    for payload in (row, detail):
        for field in ("createdAt", "updatedAt", "publishedAt"):
            assert payload[field].endswith("Z"), f"{field}={payload[field]}"
    assert detail["latest"]["publishedAt"].endswith("Z")


def test_pick_mode_is_gone_from_the_contract(client: TestClient) -> None:
    """「来源」已经不是槽位的字段：带上它要当场 422。

    来源由 `group` 决定（在容器里 = 只能从包取，否则 = 从素材挑）。**静默忽略
    更糟**：老的调用方（脚本、旧前端）以为自己设了「只从包取」，实际那一格仍然
    列散图，而界面上没有任何地方能看出这件事。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"][1]["pickMode"] = "pack"

    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 422, response.text
    assert "pickMode" in response.text


def test_pack_group_rejects_a_fixed_slot(client: TestClient) -> None:
    """固定槽位钉的是一张具体素材，放进容器等于把那句话静默删掉。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"][0]["group"] = "g1"  # source：binding=fixed

    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "只能是「用户填」" in response.json()["message"]


def test_pack_group_rejects_text_and_audio_slots(client: TestClient) -> None:
    """文本/音频不参与编号分发，包里没有它们的位置 —— 当场说清。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"].append(
        {
            "key": "voice",
            "type": "audio",
            "binding": "user",
            "refMode": "attachment",
            "minCount": 0,
            "maxCount": 1,
            "position": 3,
            "label": "人声",
            "group": "g1",
        }
    )

    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "不能放进封装容器" in response.json()["message"]


def test_pack_group_round_trips_and_keeps_members_adjacent(client: TestClient) -> None:
    """封装容器：几个槽位一个 group，进发布快照。

    使用者在模板库里只会看到一个「选包」入口 —— 点一次包，按标签把这些槽位
    一起填好。容器不是一条独立记录，就是槽位上的一列 `group`。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    for slot in draft["slots"][1:]:
        slot["group"] = "g1"

    saved = _save(client, headers, draft)
    assert [slot.get("group") for slot in saved["draft"]["slots"]] == ["", "g1", "g1"]

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert [slot.get("group") for slot in detail["latest"]["slots"]] == ["", "g1", "g1"]


def test_pack_group_members_must_be_adjacent(client: TestClient) -> None:
    """中间隔着别的槽位就不行：容器在界面上会断开，而编号仍连着。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _draft(video, first, second)
    draft["slots"][1]["group"] = "g1"
    draft["slots"][2]["group"] = "g1"
    # 在两者之间插一个独立槽位
    draft["slots"].insert(
        2,
        {
            "key": "loose",
            "type": "image",
            "binding": "user",
            "tagIds": [_TAG],
            "minCount": 0,
            "maxCount": 1,
            "position": 2,
            "label": "夹在中间",
        },
    )
    for index, slot in enumerate(draft["slots"]):
        slot["position"] = index

    response = client.post(f"{API}/validate", json=draft, headers=headers)
    assert response.status_code == 400, response.text
    assert "必须挨在一起" in response.json()["message"]


# --------------------------------------------------------------- 封装容器名
#
# 容器不是一条记录（就是槽位上相同的 `group`），它的**名字**单独存在
# `groups` 里：草稿整份进 JSON，发布时落到每一条成员行的 `group_label`。
# 这里钉四件事：名字能存能读、老版本不炸、改名算新版本、重复 key 直接 422。

_GROUP = "g1"
_GROUP_LABEL = "三视图套图"


def _grouped_draft(
    video: str,
    first: str,
    second: str,
    *,
    label: str | None = _GROUP_LABEL,
    key: str | None = None,
) -> dict[str, Any]:
    """把 `hero` 与 `note` 放进同一个容器（两者本来就相邻）。

    容器内只能放图片/视频且必须是「用户填」，这两个槽位正好符合。
    `key` 用于「同一份草稿再存一次」—— 不传就会生成一个新 spec。
    """
    draft = _draft(video, first, second, key=key)
    for slot in draft["slots"]:
        if slot["key"] in {"hero", "note"}:
            slot["group"] = _GROUP
    if label is not None:
        draft["groups"] = [{"key": _GROUP, "label": label}]
    return draft


def test_group_label_survives_publish_and_reads_back(client: TestClient) -> None:
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _grouped_draft(video, first, second)
    saved = _save(client, headers, draft)
    assert saved["draft"]["groups"] == [{"key": _GROUP, "label": _GROUP_LABEL}]

    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    # 快照里只有槽位行，名字是从成员行的 `group_label` 提炼出来的
    assert detail["latest"]["groups"] == [{"key": _GROUP, "label": _GROUP_LABEL}]


def test_group_without_a_label_reads_back_as_empty_not_missing(
    client: TestClient,
) -> None:
    """老版本没有 `group_label`（列是后加的，不回填）。

    读出来必须是**一条空名字**，不是整个 `groups` 消失 —— 前端靠「有这个 key、
    名字为空」回退成「封装槽位1」；`groups` 整段消失会让它连容器都认不出来。
    """
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _grouped_draft(video, first, second, label=None)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["groups"] == [{"key": _GROUP, "label": ""}]


def test_renaming_a_group_is_a_new_version(client: TestClient) -> None:
    """只改容器名也是内容变化：不算新版本的话，改名永远发不出去。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _grouped_draft(video, first, second)
    _save(client, headers, draft)
    client.post(f"{API}/{draft['key']}/publish", headers=headers)

    renamed = _grouped_draft(
        video, first, second, label="正面 + 三视图", key=draft["key"]
    )
    _save(client, headers, renamed)
    second_publish = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert second_publish.json()["data"]["version"] == 2

    detail = client.get(f"{API}/{draft['key']}", headers=headers).json()["data"]
    assert detail["latest"]["groups"] == [{"key": _GROUP, "label": "正面 + 三视图"}]


def test_duplicate_group_keys_are_rejected(client: TestClient) -> None:
    """同一个容器 key 两条名字：哪个算数说不清，静默取第一条等于改名没生效。"""
    headers = _headers(client)
    video, first, second = _media(client, headers)
    draft = _grouped_draft(video, first, second)
    draft["groups"] = [
        {"key": _GROUP, "label": "甲"},
        {"key": _GROUP, "label": "乙"},
    ]

    response = client.post(API, json=draft, headers=headers)

    assert response.status_code == 422, response.text
