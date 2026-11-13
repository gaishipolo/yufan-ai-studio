"""封装模板（`template_pack`）：槽位筛选约束是本模块的核心契约。

用户的需求原文：「提示词输入筛选资产库 v2 的图片-一级标签=产品，那么用户在
模板库使用时只能选择资产库 v2 的图片-一级标签=产品的素材」。

所以这里的用例全部围绕一件事：**筛选条件一旦发布，就真的约束住运行时**。

分四组：

1. **保存时的槽位校验** —— 编号连续、第 1 槽是参考视频、提示词引用对账。
2. **筛选语义的边界** —— 类别、标签、空标签；「全部匹配」不存在。
3. **运行时复核** —— 前端过滤只是 UI 便利，服务端必须再判一次。
4. **可见性与幂等** —— 草稿是私有工作区；草稿可编辑、已发布锁内容，
   同键同配置返原行、同键异配置冲突。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    insert_asset,
    make_user,
    suffix,
    upload_image,
)

PACKS = "/api/v1/template-packs"

#: 封装模板模块的权限点。用例按需增删。
PACK_RW = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:update",
    "template_pack:publish",
    "template_pack:use",
]

#: 参考视频 + 标签筛选需要的资产库权限。
PERMS = [*PACK_RW, *LIB_RW, "library:tag:manage"]


def _setup(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    """建一个作者与一个旁观者。返回 (作者请求头, 旁观者请求头)。"""
    admin = admin_headers(client)
    _, author_h = make_user(client, admin, PERMS)
    _, other_h = make_user(client, admin, PERMS)
    return author_h, other_h


def _video(client: TestClient, api: str, headers: dict[str, str]) -> str:
    """造一条视频素材（直接插行：真实 mp4 字节对这里没有意义）。"""
    return insert_asset(
        owner_id=_user_id(client, headers),
        name=f"ref-{suffix()}.mp4",
        kind="video",
        mime_type="video/mp4",
        duration=3.0,
        state="active",
    )


def _user_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/v1/identity/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _tag(client: TestClient, api: str, headers: dict[str, str], name: str) -> str:
    response = client.post(f"{api}/library/tags", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _child_tag(
    client: TestClient, api: str, headers: dict[str, str], name: str, parent: str
) -> str:
    response = client.post(
        f"{api}/library/tags",
        json={"name": name, "parentId": parent},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _tag_asset(
    client: TestClient, api: str, headers: dict[str, str], asset_id: str, tag_ids: list[str]
) -> None:
    response = client.post(
        f"{api}/library/assets/bulk",
        json={"ids": [asset_id], "action": "addTag", "payload": {"tagIds": tag_ids}},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _save(
    client: TestClient,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    key: str = "",
) -> dict[str, Any]:
    body = dict(payload)
    if key:
        body["templateKey"] = key
    response = client.post(PACKS, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _draft(
    *,
    video_id: str,
    name: str = "封装模板",
    prompt: str = "按 {{Mixed 1}} 的节奏，用 {{Mixed 2}} 做主图",
    slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一份合法的草稿载荷。"""
    return {
        "name": name,
        "prompt": prompt,
        "sourceVideoAssetId": video_id,
        "slots": slots
        if slots is not None
        else [
            {
                "token": "{{Mixed 1}}",
                "slotKind": "fixed",
                "label": "参考视频",
                "assetId": video_id,
                "mediaType": "video",
            },
            {
                "token": "{{Mixed 2}}",
                "slotKind": "filtered",
                "label": "产品主图",
                "filterKind": "image",
            },
        ],
    }


# --------------------------------------------------------------------------- #
# 1. 保存时的槽位校验
# --------------------------------------------------------------------------- #
def test_slot_numbering_must_be_continuous_from_one(client: TestClient) -> None:
    """槽位编号必须从 1 起连续。跳号会让 `{{Mixed N}}` 指向谁变得不确定。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)

    response = client.post(
        PACKS,
        json=_draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "fixed",
                    "assetId": video,
                    "mediaType": "video",
                },
                {
                    "token": "{{Mixed 3}}",
                    "slotKind": "filtered",
                    "filterKind": "image",
                },
            ],
        ),
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "连续" in response.json()["message"]


def test_first_slot_must_be_the_source_video(client: TestClient) -> None:
    """第 1 槽必须是参考视频：它是模板的一部分，不是用户能换的输入。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)

    response = client.post(
        PACKS,
        json=_draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "filtered",
                    "filterKind": "image",
                },
            ],
        ),
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "参考视频" in response.json()["message"]


def test_prompt_referencing_a_missing_slot_is_rejected(client: TestClient) -> None:
    """提示词点名了不存在的槽位编号 → 400。编译期拦住，别等生成失败。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)

    response = client.post(
        PACKS,
        json=_draft(
            video_id=video,
            prompt="用 {{Mixed 1}} 和 {{Mixed 5}} 做画面",
        ),
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "Mixed 5" in response.json()["message"]


def test_reference_video_must_really_be_a_video(client: TestClient, api: str) -> None:
    """参考视频必须是视频素材。拿图片当原片会在生成阶段才炸。"""
    author_h, _ = _setup(client)
    image = upload_image(client, api, author_h, name=f"img-{suffix()}.png")

    response = client.post(
        PACKS, json=_draft(video_id=image["id"]), headers=author_h
    )
    assert response.status_code == 400, response.text
    assert "视频" in response.json()["message"]


# --------------------------------------------------------------------------- #
# 2. 筛选语义的边界
# --------------------------------------------------------------------------- #
def test_filter_kind_rejects_text_assets(client: TestClient) -> None:
    """文本资产不是参考素材。`filterKind` 只认 image / video / audio。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)

    response = client.post(
        PACKS,
        json=_draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "fixed",
                    "assetId": video,
                    "mediaType": "video",
                },
                {
                    "token": "{{Mixed 2}}",
                    "slotKind": "filtered",
                    "filterKind": "text",
                },
            ],
        ),
        headers=author_h,
    )
    assert response.status_code == 422, response.text


def test_all_tag_match_is_not_offered(client: TestClient) -> None:
    """`all` 没有对应的执行口径（资产库筛选固定是 OR），所以不提供这个选项。

    接受它却按 OR 执行 = 承诺一个更严的筛选、实际给出更宽的结果 ——
    筛选被放宽了，而筛选条件是模板的业务契约。
    """
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)

    response = client.post(
        PACKS,
        json=_draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "fixed",
                    "assetId": video,
                    "mediaType": "video",
                },
                {
                    "token": "{{Mixed 2}}",
                    "slotKind": "filtered",
                    "filterKind": "image",
                    "filterTagIds": ["t1", "t2"],
                    "filterTagMatch": "all",
                },
            ],
        ),
        headers=author_h,
    )
    assert response.status_code == 422, response.text


def test_filtered_slot_requires_a_kind(client: TestClient) -> None:
    """筛选槽位必须声明类别。允许「不限类别」会让用户翻遍整个库才发现没一个能用。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)

    response = client.post(
        PACKS,
        json=_draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "fixed",
                    "assetId": video,
                    "mediaType": "video",
                },
                {"token": "{{Mixed 2}}", "slotKind": "filtered"},
            ],
        ),
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "类别" in response.json()["message"]


def test_candidates_only_include_matching_kind_and_tag(
    client: TestClient, api: str
) -> None:
    """候选池 = 可读 ∩ 类别对得上 ∩ 挂了指定标签 ∩ 在库。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    tag = _tag(client, api, author_h, f"产品-{suffix()}")

    hit = upload_image(client, api, author_h, name=f"hit-{suffix()}.png", color="red")
    miss = upload_image(client, api, author_h, name=f"miss-{suffix()}.png", color="blue")
    _tag_asset(client, api, author_h, hit["id"], [tag])
    _tag_asset(client, api, author_h, miss["id"], [tag])

    # 另一个类别的素材：不该出现在「图片」筛选池里。
    other = insert_asset(
        owner_id=_user_id(client, author_h),
        name=f"vid-{suffix()}.mp4",
        kind="video",
        mime_type="video/mp4",
        state="active",
    )
    _tag_asset(client, api, author_h, other, [tag])

    pack = _save(
        client,
        author_h,
        _draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "fixed",
                    "assetId": video,
                    "mediaType": "video",
                },
                {
                    "token": "{{Mixed 2}}",
                    "slotKind": "filtered",
                    "label": "产品主图",
                    "filterKind": "image",
                    "filterTagIds": [tag],
                },
            ],
        ),
    )
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.get(
        f"{PACKS}/{pack['id']}/candidates",
        params={"token": "{{Mixed 2}}"},
        headers=author_h,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    ids = [item["assetId"] for item in data["items"]]
    assert hit["id"] in ids
    assert miss["id"] in ids, "挂了标签的图就该在池子里"
    assert other not in ids, "类别不符（视频）不该出现"
    assert data["filterKind"] == "image"


def test_primary_tag_expands_to_its_children(client: TestClient, api: str) -> None:
    """选中一级标签 = 「自身 + 直属二级」（与资产库筛选口径一致）。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    parent = _tag(client, api, author_h, f"产品-{suffix()}")
    child = _child_tag(client, api, author_h, f"白底-{suffix()}", parent)

    on_child = upload_image(
        client, api, author_h, name=f"child-{suffix()}.png", color="green"
    )
    _tag_asset(client, api, author_h, on_child["id"], [child])

    pack = _save(
        client,
        author_h,
        _draft(
            video_id=video,
            slots=[
                {
                    "token": "{{Mixed 1}}",
                    "slotKind": "fixed",
                    "assetId": video,
                    "mediaType": "video",
                },
                {
                    "token": "{{Mixed 2}}",
                    "slotKind": "filtered",
                    "filterKind": "image",
                    "filterTagIds": [parent],
                },
            ],
        ),
    )
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.get(
        f"{PACKS}/{pack['id']}/candidates",
        params={"token": "{{Mixed 2}}"},
        headers=author_h,
    )
    assert response.status_code == 200, response.text
    ids = [item["assetId"] for item in response.json()["data"]["items"]]
    assert on_child["id"] in ids, "挂在一级下的二级标签也算命中"


# --------------------------------------------------------------------------- #
# 3. 运行时复核
# --------------------------------------------------------------------------- #
def test_fixed_slot_has_no_candidate_pool(client: TestClient) -> None:
    """固定素材槽位没有可选范围。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video))
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.get(
        f"{PACKS}/{pack['id']}/candidates",
        params={"token": "{{Mixed 1}}"},
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "固定素材" in response.json()["message"]


def test_unpublished_pack_hides_its_candidates_from_others(
    client: TestClient,
) -> None:
    """草稿是私有工作区：别人连候选池都看不到。"""
    author_h, other_h = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video))

    response = client.get(
        f"{PACKS}/{pack['id']}/candidates",
        params={"token": "{{Mixed 2}}"},
        headers=other_h,
    )
    assert response.status_code == 400, response.text


def _filtered_pack(
    client: TestClient,
    api: str,
    headers: dict[str, str],
    *,
    tag_ids: list[str] | None = None,
    filter_kind: str = "image",
) -> dict[str, Any]:
    """造一条带筛选槽位并已发布的封装模板。"""
    video = _video(client, api, headers)
    slots: list[dict[str, Any]] = [
        {
            "token": "{{Mixed 1}}",
            "slotKind": "fixed",
            "assetId": video,
            "mediaType": "video",
        },
        {
            "token": "{{Mixed 2}}",
            "slotKind": "filtered",
            "label": "产品主图",
            "filterKind": filter_kind,
        },
    ]
    if tag_ids is not None:
        slots[1]["filterTagIds"] = tag_ids
    pack = _save(client, headers, _draft(video_id=video, slots=slots))
    client.post(f"{PACKS}/{pack['id']}/publish", headers=headers)
    return pack


def test_verify_accepts_a_conforming_pick(client: TestClient, api: str) -> None:
    """合规选材通过复核。"""
    author_h, _ = _setup(client)
    tag = _tag(client, api, author_h, f"产品-{suffix()}")
    hit = upload_image(client, api, author_h, name=f"hit-{suffix()}.png", color="red")
    _tag_asset(client, api, author_h, hit["id"], [tag])
    pack = _filtered_pack(client, api, author_h, tag_ids=[tag])

    response = client.post(
        f"{PACKS}/{pack['id']}/verify",
        json={"picks": {"{{Mixed 2}}": hit["id"]}},
        headers=author_h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["picks"] == {"{{Mixed 2}}": hit["id"]}


def test_verify_rejects_a_pick_that_breaks_the_filter(
    client: TestClient, api: str
) -> None:
    """越权提交被拒：**这才是筛选条件的价值所在**。

    用户在模板库里看到的「只能选产品标签的图」如果只在界面上生效，
    拿个接口一调就能绕过 —— 那模板承诺的约束就是假的。
    """
    author_h, _ = _setup(client)
    tag = _tag(client, api, author_h, f"产品-{suffix()}")
    good = upload_image(client, api, author_h, name=f"good-{suffix()}.png", color="red")
    bad = upload_image(client, api, author_h, name=f"bad-{suffix()}.png", color="blue")
    _tag_asset(client, api, author_h, good["id"], [tag])
    # `bad` 不挂标签 —— 不满足筛选条件。
    pack = _filtered_pack(client, api, author_h, tag_ids=[tag])

    response = client.post(
        f"{PACKS}/{pack['id']}/verify",
        json={"picks": {"{{Mixed 2}}": bad["id"]}},
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "不符合" in response.json()["message"]


def test_verify_rejects_a_pick_of_the_wrong_kind(client: TestClient, api: str) -> None:
    """类别对不上也拒：筛选说「图片」就不能选视频。"""
    author_h, _ = _setup(client)
    video = _video(client, api, author_h)
    pack = _filtered_pack(client, api, author_h, filter_kind="image")

    response = client.post(
        f"{PACKS}/{pack['id']}/verify",
        json={"picks": {"{{Mixed 2}}": video}},
        headers=author_h,
    )
    assert response.status_code == 400, response.text


def test_verify_rejects_an_unknown_slot(client: TestClient, api: str) -> None:
    """模板里没有的槽位 → 拒。多半是前端把别的模板的选材串过来了。"""
    author_h, _ = _setup(client)
    pack = _filtered_pack(client, api, author_h)

    response = client.post(
        f"{PACKS}/{pack['id']}/verify",
        json={"picks": {"{{Mixed 9}}": "whatever"}},
        headers=author_h,
    )
    assert response.status_code == 400, response.text


def test_verify_ignores_fixed_slots(client: TestClient, api: str) -> None:
    """固定槽位不参与复核：它没有「选」这回事，素材是钉死的。"""
    author_h, _ = _setup(client)
    tag = _tag(client, api, author_h, f"产品-{suffix()}")
    hit = upload_image(client, api, author_h, name=f"hit-{suffix()}.png", color="red")
    _tag_asset(client, api, author_h, hit["id"], [tag])
    pack = _filtered_pack(client, api, author_h, tag_ids=[tag])

    response = client.post(
        f"{PACKS}/{pack['id']}/verify",
        json={"picks": {"{{Mixed 2}}": hit["id"], "{{Mixed 1}}": "whatever"}},
        headers=author_h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["picks"] == {"{{Mixed 2}}": hit["id"]}


def test_verify_rejects_a_required_slot_left_empty(
    client: TestClient, api: str
) -> None:
    """必填槽位没选 → 拒。提示词会指向一个空槽位。"""
    author_h, _ = _setup(client)
    pack = _filtered_pack(client, api, author_h)

    response = client.post(
        f"{PACKS}/{pack['id']}/verify",
        json={"picks": {"{{Mixed 2}}": ""}},
        headers=author_h,
    )
    assert response.status_code == 400, response.text
    assert "必须" in response.json()["message"]


# --------------------------------------------------------------------------- #
# 4. 可见性与幂等
# --------------------------------------------------------------------------- #
def test_others_cannot_list_someone_elses_draft(client: TestClient) -> None:
    """传别人的 `ownerId` 也拿不到别人的草稿。

    草稿里有提示词原文与筛选条件。权限点只管「能不能看封装模板」，
    管不了「能不能看某个人还没发布的草稿」—— 所以这条得在服务层挡。
    """
    author_h, other_h = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video, name="我的私有草稿"))
    author_id = _user_id(client, author_h)

    # 别人的草稿不该出现在公开列表里。
    response = client.get(PACKS, headers=other_h)
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert pack["id"] not in ids

    # 传别人的 ownerId 也不行。
    response = client.get(
        PACKS, params={"ownerId": author_id, "status": "draft"}, headers=other_h
    )
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert pack["id"] not in ids, "别人的草稿是私有工作区"

    # 作者自己看得到。
    response = client.get(
        PACKS, params={"ownerId": author_id, "status": "draft"}, headers=author_h
    )
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert pack["id"] in ids


def test_same_key_same_config_is_idempotent(client: TestClient) -> None:
    """同键同配置返回原行：脚本重试不产生副本。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    key = f"tp-{suffix()}"

    first = _save(client, author_h, _draft(video_id=video), key=key)
    second = _save(client, author_h, _draft(video_id=video), key=key)
    assert first["id"] == second["id"]
    assert second["status"] == "draft"


def test_draft_can_be_edited_under_the_same_key(client: TestClient) -> None:
    """草稿是作者的私有工作区：同 key 再存一次是「编辑」，不是冲突。

    这一点与 `modules/template` 不同 —— 那边是一次性创建，没有草稿态，
    所以同键异配置一律 409。这里有草稿→发布生命周期：编辑页里改一个字
    再保存是**正常编辑动作**，若报 409，用户根本没法编辑自己的草稿。

    幂等性依然成立：反复保存返回的是**同一个 id**，不产生副本。
    """
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    key = f"tp-{suffix()}"

    first = _save(client, author_h, _draft(video_id=video, name="原版"), key=key)
    second = _save(client, author_h, _draft(video_id=video, name="改过的版本"), key=key)

    assert first["id"] == second["id"], "编辑不该产生第二条模板"
    assert second["name"] == "改过的版本", "内容要真的更新掉"
    assert second["status"] == "draft"


def test_published_pack_same_key_different_config_conflicts(
    client: TestClient,
) -> None:
    """已发布后内容锁定：同键异配置 409。

    静默覆盖会让脚本重试把别人已经引用过的模板改掉 —— 「模板当时长什么样」
    与「现在长什么样」对不上。
    """
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    key = f"tp-{suffix()}"
    pack = _save(client, author_h, _draft(video_id=video, name="原版"), key=key)
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.post(
        PACKS,
        json={**_draft(video_id=video, name="改过的版本"), "templateKey": key},
        headers=author_h,
    )
    assert response.status_code == 409, response.text


def test_published_pack_same_key_same_config_is_idempotent(
    client: TestClient,
) -> None:
    """已发布后同键同配置仍返回原行：脚本重试是幂等的。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    key = f"tp-{suffix()}"
    pack = _save(client, author_h, _draft(video_id=video, name="原版"), key=key)
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.post(
        PACKS,
        json={**_draft(video_id=video, name="原版"), "templateKey": key},
        headers=author_h,
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["id"] == pack["id"]
    assert response.json()["data"]["status"] == "published"


def test_someone_elses_key_cannot_be_claimed(client: TestClient) -> None:
    """别人的 key 不能被抢走：连「这个 key 存在」都不该确认。

    确认存在就等于给出命名空间探测能力 —— 拿到「被占用」这个信息，
    就能靠遍历 key 摸清别人的命名空间。
    """
    author_h, other_h = _setup(client)
    video = _video(client, PACKS, author_h)
    key = f"tp-{suffix()}"
    _save(client, author_h, _draft(video_id=video), key=key)

    response = client.post(
        PACKS,
        json={**_draft(video_id=video), "templateKey": key},
        headers=other_h,
    )
    assert response.status_code == 409, response.text
    assert "占用" in response.json()["message"]


def test_published_pack_cannot_be_edited(client: TestClient) -> None:
    """已发布的模板不能再改内容：它可能已被别人引用。"""
    author_h, _ = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video))
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.patch(
        f"{PACKS}/{pack['id']}/prompt",
        json={"prompt": "换个说法 {{Mixed 1}} {{Mixed 2}}"},
        headers=author_h,
    )
    assert response.status_code == 409, response.text


def test_publishing_requires_ownership(client: TestClient) -> None:
    """只能发布自己创建的封装模板。"""
    author_h, other_h = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video))

    response = client.post(f"{PACKS}/{pack['id']}/publish", headers=other_h)
    assert response.status_code == 400, response.text


def test_published_pack_shows_up_in_the_public_list(client: TestClient) -> None:
    """发布后进模板库，别人看得到。"""
    author_h, other_h = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video, name="公开模板"))
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)

    response = client.get(PACKS, headers=other_h)
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert pack["id"] in ids


def test_disabled_pack_leaves_the_public_list(client: TestClient) -> None:
    """停用后不再对外。"""
    author_h, other_h = _setup(client)
    video = _video(client, PACKS, author_h)
    pack = _save(client, author_h, _draft(video_id=video))
    client.post(f"{PACKS}/{pack['id']}/publish", headers=author_h)
    client.post(f"{PACKS}/{pack['id']}/disable", headers=author_h)

    response = client.get(PACKS, headers=other_h)
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert pack["id"] not in ids
